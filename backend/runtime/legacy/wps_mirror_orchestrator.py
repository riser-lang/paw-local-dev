#!/usr/bin/env python3
"""R15-B1 WPS Mirror V1 orchestrator.

Pipeline: inventory object -> production eligibility -> exact dispatch ->
incremental decision -> acquisition -> validation -> immutable local version
-> current state -> SourceVersion handoff record.

The legacy ``ksheet_server_worker.py`` is not imported or modified here.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from wps_file_registry import derive_acquisition_route, semantic_classification
from wps_mirror_acquisition import ProductionAcquisitionResult, acquire_production
from wps_mirror_contract import (
    BACKEND_FORMATS,
    ProductionEligibility,
    dispatch_backend,
    dispatch_acquisition_route,
    exact_ready_set_is_valid,
    production_eligibility,
)
from wps_mirror_state import (
    MirrorArtifactStore,
    MirrorState,
    MirrorStateStore,
    decide_incremental_identity,
    is_cold_evicted_receipt,
    sha256_file,
    utc_now,
    reconcile_inventory_coverage,
    reconcile_ksheet_coverage,
    is_ksheet_item,
    is_airpage_item,
)


@dataclass(frozen=True)
class SourceVersionHandoff:
    object_id: str
    provider: str
    source_version: str
    sha256: str
    artifact_path: str
    format: str
    mime_type: str
    observed_at: str
    display_name: str
    parent_id: str
    change_type: str


@dataclass
class MirrorOutcome:
    object_id: str
    status: str
    change_type: str = ""
    backend: str = ""
    error_class: str = ""
    handoff: SourceVersionHandoff | None = None
    terminal_routing_state: str = ""


def _source_version(record: Mapping[str, Any]) -> str:
    return str(record.get("version", record.get("source_version", "")) or "")


def _base_state(record: Mapping[str, Any], backend: str = "") -> dict[str, Any]:
    return {
        "object_id": str(record.get("object_id", "") or ""),
        "file_type": str(record.get("file_type", "") or ""),
        "backend": backend or str(record.get("mirror_backend_candidate", "") or ""),
        "source_version": _source_version(record),
        "source_mtime": str(record.get("mtime", "") or ""),
        "source_size": int(record.get("size", 0) or 0),
        "display_name": str(record.get("display_name", "") or ""),
        "parent_id": str(record.get("parent_id", "") or ""),
        "semantic_classification": str(record.get("semantic_classification", "UNKNOWN") or "UNKNOWN"),
        "acquisition_route": str(record.get("acquisition_route", "") or ""),
        "routing_evidence": str(record.get("routing_evidence", "") or ""),
        "link_id": str(record.get("link_id", "") or ""),
    }


def normalize_inventory_object(item: Mapping[str, Any]) -> dict[str, Any]:
    """Map the existing inventory/search shapes into the B1 object contract."""
    if not isinstance(item, Mapping):
        return {}
    version_metadata = item.get("version_metadata") if isinstance(item.get("version_metadata"), Mapping) else {}
    value = dict(item)
    value.setdefault("object_id", item.get("document_id", item.get("stable_document_id", "")))
    value.setdefault("display_name", item.get("title", item.get("fname", "")))
    value.setdefault("filename", value.get("display_name", ""))
    value.setdefault("parent_id", item.get("parent_id", ""))
    value.setdefault("version", version_metadata.get("version", item.get("version", "")))
    value.setdefault("mtime", item.get("modified_at", item.get("mtime", "")))
    if not value.get("cloud_locator"):
        value["cloud_locator"] = item.get("locator", "")
    value.setdefault("object_type", "file")
    if "provider_metadata" not in value:
        value["provider_metadata"] = {
            "provider_path": item.get("provider_path", "wps_ui_search_v6" if item.get("provider_namespace") else ""),
            "provider_namespace": item.get("provider_namespace"),
            "provider_object_type": item.get("provider_object_type", "file"),
            "provider_filename": item.get("provider_filename", value.get("filename", "")),
        }
    semantic, family = semantic_classification(value)
    value["semantic_classification"] = semantic
    value["file_type"] = semantic
    value["file_family"] = family
    route = derive_acquisition_route(value)
    value["acquisition_route"] = route["route"]
    value["routing_evidence"] = route["evidence"]
    # Migrate the v1.0 media marker at the acquisition boundary.  Media is
    # not an exclusion rule under R15-B1.5; it is raw-persisted when the
    # provider capability is proven, otherwise it receives an exact blocker.
    if value.get("mirror_policy") == "MEDIA_EXCLUDED":
        value["mirror_policy"] = "INCLUDED"
        value["mirror_exclusion_rule"] = None
        value["mirror_exclusion_reason"] = None
    # The route is authoritative for acquisition.  Replace stale v1-era
    # candidates (including media NO_MIRROR) with the one computed route.
    value["mirror_backend_candidate"] = route["backend"]
    if not value.get("cloud_locator") and value.get("link_id"):
        value["cloud_locator"] = "https://365.kdocs.cn/l/" + str(value["link_id"])
    return value


def airpage_discovery_completeness(inventory_result: Mapping[str, Any] | None) -> str:
    """Return PASS only for an explicit, independently proven all-space flag."""
    if not isinstance(inventory_result, Mapping):
        return "PARTIAL"
    return "PASS" if (
        inventory_result.get("airpage_discovery_complete") == "YES"
        and inventory_result.get("true_team_space_enumeration") == "YES"
        and inventory_result.get("team_space_inventory_available") == "YES"
        and not inventory_result.get("errors")
    ) else "PARTIAL"


class MirrorOrchestrator:
    def __init__(
        self,
        state_store: MirrorStateStore,
        artifact_store: MirrorArtifactStore,
        acquirer: Callable[[Mapping[str, Any], str], Any] | None = None,
        provider: str = "wps",
        retention_ledger: Any | None = None,
        retention_checkpoint: Mapping[str, Any] | None = None,
    ):
        self.state = state_store
        self.artifacts = artifact_store
        self.acquirer = acquirer
        self.provider = provider
        self.retention_ledger = retention_ledger
        self.retention_checkpoint = dict(retention_checkpoint or {})

    def _persist_noneligible(self, record: Mapping[str, Any], eligibility: ProductionEligibility) -> MirrorOutcome:
        base = _base_state(record, eligibility.backend)
        prior = self.state.get(base["object_id"]) if base["object_id"] else None
        if prior:
            base.update({
                "artifact_path": prior.artifact_path,
                "artifact_size": prior.artifact_size,
                "sha256": prior.sha256,
                "last_success_at": prior.last_success_at,
            })
        terminal = "EXCLUDED_INVENTORY_ONLY" if eligibility.status == "EXCLUDED" else "BLOCKED_WITH_EXACT_REASON"
        self.state.upsert({**base, "status": eligibility.status, "error_class": eligibility.reason, "terminal_routing_state": terminal})
        return MirrorOutcome(base["object_id"], eligibility.status, backend=eligibility.backend, error_class=eligibility.reason, terminal_routing_state=terminal)

    def _local_validation(self, path: Path, backend: str, expected_size: int = 0) -> dict[str, Any]:
        if not path.is_file():
            return {"format_validation_pass": False, "error_class": "ARTIFACT_MISSING"}
        if backend == "WPS_SPREADSHEET_EXPORT_XLSX":
            from wps_export_acquisition import validate_export_xlsx_artifact
            return validate_export_xlsx_artifact(str(path))
        if backend == "NATIVE_XLSX_DIRECT":
            from wps_native_acquisition import validate_xlsx_artifact
            result = validate_xlsx_artifact(str(path), expected_size=expected_size)
            result["format_validation_pass"] = all(result.get(key, False) for key in ("artifact_present", "artifact_nonzero", "xlsx_signature_valid", "xlsx_zip_valid", "content_types_present", "workbook_xml_present", "openpyxl_read_valid")) and not result.get("is_html_body", False) and not result.get("is_json_body", False)
            return result
        if backend in {"NATIVE_DOCX_DIRECT", "WPS_DOCUMENT_EXPORT_DOCX"}:
            if backend == "WPS_DOCUMENT_EXPORT_DOCX":
                from wps_airpage_export_acquisition import validate_airpage_docx_artifact
                return validate_airpage_docx_artifact(path)
            from wps_native_acquisition import validate_docx_artifact
            result = validate_docx_artifact(str(path), expected_size=expected_size)
            result["format_validation_pass"] = all(result.get(key, False) for key in ("artifact_present", "artifact_nonzero", "docx_signature_valid", "docx_zip_valid", "content_types_present", "document_xml_present", "python_docx_read_valid")) and not result.get("is_html_body", False) and not result.get("is_json_body", False)
            return result
        if backend == "NATIVE_PPTX_DIRECT":
            from wps_native_acquisition import validate_pptx_artifact
            result = validate_pptx_artifact(str(path), expected_size=expected_size)
            result["format_validation_pass"] = all(result.get(key, False) for key in ("artifact_present", "artifact_nonzero", "pptx_signature_valid", "pptx_zip_valid", "content_types_present", "presentation_xml_present", "python_pptx_read_valid")) and not result.get("is_html_body", False) and not result.get("is_json_body", False)
            return result
        if backend == "PDF_DIRECT":
            from wps_native_acquisition import validate_pdf_artifact
            result = validate_pdf_artifact(str(path), expected_size=expected_size)
            result["format_validation_pass"] = all(result.get(key, False) for key in ("artifact_present", "artifact_nonzero", "pdf_header_valid", "pdf_eof_marker_present", "pdf_parser_read_valid", "pdf_page_count")) and not result.get("is_html_body", False) and not result.get("is_json_body", False)
            return result
        if backend == "RAW_BINARY_DIRECT":
            size = path.stat().st_size
            expected = int(expected_size or 0)
            return {"artifact_present": True, "artifact_nonzero": size > 0, "artifact_size": size, "sha256": sha256_file(path), "source_size_match": "YES" if expected and size == expected else ("NO" if expected else "NOT_COMPARABLE"), "format_validation_pass": size > 0 and (not expected or size == expected)}
        return {"format_validation_pass": False, "error_class": "BLOCKED_BACKEND"}

    def _normalize_acquisition(self, raw: Any, record: Mapping[str, Any], staging_dir: Path, backend: str) -> tuple[str, Path | None, str, str, str]:
        if isinstance(raw, (bytes, bytearray)):
            file_format, _mime = BACKEND_FORMATS.get(backend, ("bin", "application/octet-stream"))
            if backend == "RAW_BINARY_DIRECT":
                name = str(record.get("filename") or record.get("display_name") or "")
                if "." in name.rsplit("/", 1)[-1]:
                    file_format = name.rsplit(".", 1)[-1].casefold()
            staged = self.artifacts.stage_bytes("inline", str(record["object_id"]), "source." + file_format, bytes(raw))
            return "ACQUIRED", staged, file_format, _mime, ""
        if isinstance(raw, ProductionAcquisitionResult):
            data = raw.sanitized()
        elif isinstance(raw, Mapping):
            data = dict(raw)
        else:
            data = {key: getattr(raw, key, "") for key in ("status", "artifact_path", "format", "mime_type", "error_class")}
        status = str(data.get("status", "FAILED") or "FAILED")
        artifact = str(data.get("artifact_path", "") or "")
        path = Path(artifact) if artifact else None
        if path is not None:
            try:
                self.artifacts._assert_staged(path)
            except (ValueError, OSError):
                # A backend may expose its own download/cache path.  Copy the
                # returned regular file into this run's staging area before
                # validation/finalization; never commit an external path.
                try:
                    source = path.resolve(strict=True)
                    if not source.is_file():
                        return "FAILED", None, "", "", "ARTIFACT_MISSING"
                    file_format, _mime = BACKEND_FORMATS.get(backend, ("bin", "application/octet-stream"))
                    if backend == "RAW_BINARY_DIRECT":
                        name = str(record.get("filename") or record.get("display_name") or "")
                        if "." in name.rsplit("/", 1)[-1]:
                            file_format = name.rsplit(".", 1)[-1].casefold()
                    staged_path = staging_dir / ("source." + file_format)
                    temporary = staging_dir / (".materialize-" + file_format + ".part")
                    shutil.copyfile(source, temporary)
                    os.replace(temporary, staged_path)
                    self.artifacts._assert_staged(staged_path)
                    path = staged_path
                    data["artifact_path"] = str(staged_path)
                except (OSError, ValueError):
                    return "FAILED", None, "", "", "ARTIFACT_OUTSIDE_STAGING"
        file_format, mime_type = BACKEND_FORMATS.get(backend, ("bin", "application/octet-stream"))
        if backend == "RAW_BINARY_DIRECT" and not data.get("format"):
            name = str(record.get("filename") or record.get("display_name") or "")
            file_format = name.rsplit(".", 1)[-1].casefold() if "." in name.rsplit("/", 1)[-1] else "bin"
        return status, path, str(data.get("format") or file_format), str(data.get("mime_type") or mime_type), str(data.get("error_class", "") or "")

    def _persist_evicted_analyzed(
        self,
        record: Mapping[str, Any],
        eligibility: ProductionEligibility,
        previous: MirrorState | None,
        receipt: Mapping[str, Any] | None,
        change_type: str = "UNCHANGED",
    ) -> MirrorOutcome:
        """Persist cold ANALYZED_HASH_RECEIPT without requiring a local raw path."""
        base = _base_state(record, eligibility.backend)
        object_id = base["object_id"]
        receipt = dict(receipt or {})
        sha256 = str(receipt.get("sha256") or (previous.sha256 if previous else "") or "")
        artifact_size = int(receipt.get("original_size") or (previous.artifact_size if previous else 0) or 0)
        # Cold retention must not require a live raw path. Duplicate bytes, if
        # present, are evicted by the retention sweep using the durable receipt.
        artifact_path = ""
        self.state.upsert({
            **base,
            "artifact_path": artifact_path,
            "artifact_size": artifact_size,
            "sha256": sha256,
            "status": "EVICTED_ANALYZED",
            "error_class": "",
            "terminal_routing_state": "ANALYZED_HASH_RECEIPT",
            "last_success_at": (previous.last_success_at if previous and previous.last_success_at else utc_now()),
        })
        file_format, mime_type = BACKEND_FORMATS.get(eligibility.backend, ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"))
        handoff = SourceVersionHandoff(
            object_id, self.provider, _source_version(record) or str(receipt.get("source_version") or "").split("@", 1)[-1],
            sha256, artifact_path, file_format, mime_type, utc_now(), base["display_name"], base["parent_id"], change_type,
        )
        return MirrorOutcome(object_id, "UNCHANGED", change_type, eligibility.backend, handoff=handoff, terminal_routing_state="ANALYZED_HASH_RECEIPT")

    def _maybe_skip_evicted_pptx(
        self,
        record: Mapping[str, Any],
        eligibility: ProductionEligibility,
        previous: MirrorState | None,
    ) -> MirrorOutcome | None:
        """Skip acquisition when a durable EVICTED_ANALYZED receipt still matches."""
        if eligibility.backend != "NATIVE_PPTX_DIRECT" or self.retention_ledger is None:
            return None
        from pptx_retention_manager import acquisition_decision
        decision, receipt = acquisition_decision(record, self.retention_checkpoint, self.retention_ledger)
        if decision != "UNCHANGED_EVICTED_ANALYZED":
            return None
        return self._persist_evicted_analyzed(record, eligibility, previous, receipt, "UNCHANGED")

    def process_object(self, record: Mapping[str, Any], page: Any = None, run_id: str | None = None) -> MirrorOutcome:
        record = normalize_inventory_object(record)
        object_id = str(record.get("object_id", "") or "") if isinstance(record, Mapping) else ""
        previous = self.state.get(object_id) if object_id else None
        if object_id:
            discovered = {**_base_state(record), "status": "DISCOVERED", "error_class": ""}
            if previous:
                discovered.update({
                    "artifact_path": previous.artifact_path,
                    "artifact_size": previous.artifact_size,
                    "sha256": previous.sha256,
                    "last_success_at": previous.last_success_at,
                })
            self.state.upsert(discovered)
        eligibility = production_eligibility(record)
        if not eligibility.eligible:
            return self._persist_noneligible(record, eligibility)
        if not exact_ready_set_is_valid():
            return self._persist_noneligible(record, ProductionEligibility(False, "BLOCKED_BACKEND", object_id, eligibility.file_type, eligibility.backend, "READY_SET_NOT_EXACT", "", eligibility.semantic_classification, eligibility.acquisition_route, eligibility.routing_evidence))
        skipped = self._maybe_skip_evicted_pptx(record, eligibility, previous)
        if skipped is not None:
            return skipped
        change_type = decide_incremental_identity(record, previous)
        base = _base_state(record, eligibility.backend)
        if change_type in {"UNCHANGED", "RENAMED", "MOVED"}:
            if previous is None:
                change_type = "NEW"
            elif not previous.artifact_path and not is_cold_evicted_receipt(previous):
                change_type = "NEW"
            elif is_cold_evicted_receipt(previous):
                return self._persist_evicted_analyzed(record, eligibility, previous, {"sha256": previous.sha256, "original_size": previous.artifact_size, "source_version": previous.source_version}, change_type)
            else:
                self.state.upsert({**asdict(previous), **base, "status": "UNCHANGED", "error_class": ""})
                handoff = SourceVersionHandoff(object_id, self.provider, previous.source_version, previous.sha256, previous.artifact_path, BACKEND_FORMATS.get(eligibility.backend, ("bin", "application/octet-stream"))[0], BACKEND_FORMATS.get(eligibility.backend, ("bin", "application/octet-stream"))[1], utc_now(), base["display_name"], base["parent_id"], change_type)
                return MirrorOutcome(object_id, "UNCHANGED", change_type, eligibility.backend, handoff=handoff, terminal_routing_state="COMMITTED_LOCAL")

        pending = {**base, "status": "ELIGIBLE", "error_class": ""}
        if previous:
            # Keep the prior committed artifact referenced while a new source
            # version is in flight; a failure must not erase current state.
            pending.update({
                "artifact_path": previous.artifact_path,
                "artifact_size": previous.artifact_size,
                "sha256": previous.sha256,
                "last_success_at": previous.last_success_at,
            })
        self.state.upsert(pending)
        attempt_at = utc_now()
        self.state.update_status(object_id, "ACQUIRING", last_attempt_at=attempt_at)
        run = run_id or uuid.uuid4().hex
        staging_dir = self.artifacts.staging_dir(run, object_id)
        try:
            raw = (self.acquirer or (lambda obj, stage: acquire_production(page, dict(obj), stage)))(record, str(staging_dir))
            status, staged, file_format, mime_type, error_class = self._normalize_acquisition(raw, record, staging_dir, eligibility.backend)
            if status not in {"ACQUIRED", "SUCCESS"} or staged is None or not staged.is_file():
                self.state.update_status(object_id, "FAILED", last_attempt_at=attempt_at, error_class=error_class or "ACQUISITION_FAILED", terminal_routing_state="BLOCKED_WITH_EXACT_REASON")
                return MirrorOutcome(object_id, "FAILED", change_type, eligibility.backend, error_class or "ACQUISITION_FAILED", terminal_routing_state="BLOCKED_WITH_EXACT_REASON")
            self.state.update_status(object_id, "ACQUIRED", last_attempt_at=attempt_at)
            validation = self._local_validation(staged, eligibility.backend, int(record.get("size", 0) or 0))
            if not validation.get("format_validation_pass"):
                self.state.update_status(object_id, "FAILED", last_attempt_at=attempt_at, error_class="FORMAT_VALIDATION_FAILED")
                self.state.update_status(object_id, "FAILED", last_attempt_at=attempt_at, error_class="FORMAT_VALIDATION_FAILED", terminal_routing_state="BLOCKED_WITH_EXACT_REASON")
                return MirrorOutcome(object_id, "FAILED", change_type, eligibility.backend, "FORMAT_VALIDATION_FAILED", terminal_routing_state="BLOCKED_WITH_EXACT_REASON")
            self.state.update_status(object_id, "VALIDATED", last_attempt_at=attempt_at)
            artifact_name = "artifact." + file_format
            final_path, artifact_size, digest = self.artifacts.finalize(staged, object_id, _source_version(record), artifact_name)
            success_at = utc_now()
            self.state.update_status(object_id, "COMMITTED_LOCAL", artifact_path=str(final_path), artifact_size=artifact_size, sha256=digest, last_attempt_at=attempt_at, last_success_at=success_at, error_class="", terminal_routing_state="COMMITTED_LOCAL")
            handoff = SourceVersionHandoff(object_id, self.provider, _source_version(record), digest, str(final_path), file_format, mime_type, success_at, base["display_name"], base["parent_id"], change_type)
            return MirrorOutcome(object_id, "COMMITTED_LOCAL", change_type, eligibility.backend, handoff=handoff, terminal_routing_state="COMMITTED_LOCAL")
        except Exception as exc:
            self.state.update_status(object_id, "FAILED", last_attempt_at=attempt_at, error_class=type(exc).__name__, terminal_routing_state="BLOCKED_WITH_EXACT_REASON")
            return MirrorOutcome(object_id, "FAILED", change_type, eligibility.backend, type(exc).__name__, terminal_routing_state="BLOCKED_WITH_EXACT_REASON")

    def dispatch_inventory(self, objects: Iterable[Mapping[str, Any]], max_workers: int = 8) -> list[ProductionEligibility]:
        """Resolve routes in parallel without touching provider content."""
        values = list(objects)
        def resolve(record):
            return production_eligibility(normalize_inventory_object(record))
        with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
            return list(pool.map(resolve, values))

    def process_inventory(self, objects: Iterable[Mapping[str, Any]], page: Any = None, run_id: str | None = None, parallel: bool = False, max_workers: int = 4) -> list[MirrorOutcome]:
        values = list(objects)
        if not parallel:
            return [self.process_object(record, page=page, run_id=run_id) for record in values]
        # Browser pages are not thread-safe. Parallel acquisition is therefore
        # available only to callers supplying a thread-safe/custom acquirer.
        if self.acquirer is None and page is not None:
            return [MirrorOutcome(str(record.get("object_id", "") or ""), "FAILED", error_class="PARALLEL_BROWSER_UNSAFE", terminal_routing_state="BLOCKED_WITH_EXACT_REASON") for record in values]
        with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
            return list(pool.map(lambda record: self.process_object(record, page=None, run_id=run_id), values))

    def reconcile_inventory(self, inventory: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        return reconcile_inventory_coverage(list(inventory), self.state.all())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="WPS Mirror V1 orchestrator")
    parser.add_argument("command", choices=("run",))
    parser.add_argument("--db", default="/srv/paw/wps_mirror/state/mirror_state.sqlite3")
    parser.add_argument("--mirror-root", default="/srv/paw/wps_mirror")
    parser.add_argument("--inventory-json", default="")
    args = parser.parse_args(argv)
    if not args.inventory_json:
        print("ORCHESTRATOR_READY=YES")
        print("FULL_MIRROR_PRODUCTION_READY=NO")
        print("ERROR=INVENTORY_PROVIDER_NOT_CONFIGURED")
        return 2
    with open(args.inventory_json, "r", encoding="utf-8") as handle:
        objects = json.load(handle)
    if isinstance(objects, Mapping):
        objects = objects.get("documents", [])
    orchestrator = MirrorOrchestrator(MirrorStateStore(args.db), MirrorArtifactStore(args.mirror_root))
    outcomes = orchestrator.process_inventory(objects)
    print(json.dumps([asdict(outcome) for outcome in outcomes], ensure_ascii=False, default=str))
    return 0 if all(outcome.status not in {"FAILED"} for outcome in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
