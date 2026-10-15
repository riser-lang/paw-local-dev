"""PAW Local Source Watcher - Monitors local directory for document changes.

Pipeline:
File Change -> Debounce -> Reader Fidelity -> Evidence -> Provider -> Candidate -> SQLite
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from backend.core.config import EVIDENCE_DIR, SOURCES_DIR, settings
from backend.governance.service import governance
from backend.provider.adapter import get_active_provider
from backend.runtime_core.project_convergence import run_project_convergence
from backend.reader.contract import ReaderInput, artifact_sha256
from backend.reader.dispatch import read_artifact
from backend.workspace_api.repository import list_knowledge, list_projects

logger = logging.getLogger("paw.watcher")

SUPPORTED_EXTENSIONS = {".docx", ".xlsx", ".xlsm", ".pptx", ".pdf"}


def _is_temporary_file(path: Path) -> bool:
    name = path.name
    return name.startswith("~$") or name.startswith(".") or name.endswith(".tmp")


def _wait_for_file_settle(path: Path, timeout: float = 3.0, check_interval: float = 0.3) -> bool:
    """Wait until file is not locked and size is stable."""
    start = time.time()
    last_size = -1
    while time.time() - start < timeout:
        if not path.is_file():
            return False
        try:
            current_size = path.stat().st_size
            if current_size == last_size and current_size > 0:
                # Test opening for reading
                with open(path, "rb"):
                    return True
            last_size = current_size
        except OSError:
            pass
        time.sleep(check_interval)
    return path.is_file() and path.stat().st_size > 0


class SourceChangeHandler(FileSystemEventHandler):
    def __init__(self, watcher: "LocalSourceWatcher") -> None:
        super().__init__()
        self.watcher = watcher

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.watcher.handle_file_event(Path(event.src_path), event_type="created")

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.watcher.handle_file_event(Path(event.src_path), event_type="modified")

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory and hasattr(event, "dest_path"):
            self.watcher.handle_file_event(Path(event.dest_path), event_type="moved")


class LocalSourceWatcher:
    def __init__(self, sources_dir: Optional[Path] = None, evidence_dir: Optional[Path] = None) -> None:
        self.sources_dir = Path(sources_dir or SOURCES_DIR)
        self.sources_dirs: List[Path] = [self.sources_dir]
        self.evidence_dir = Path(evidence_dir or EVIDENCE_DIR)
        self.observer: Optional[Observer] = None
        self._processed_hashes: Set[str] = set()
        self._lock = threading.Lock()
        self.last_events: List[Dict[str, Any]] = []

    def start(self) -> None:
        if self.observer and self.observer.is_alive():
            return

        dirs = list(getattr(self, "sources_dirs", None) or [self.sources_dir])
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

        self.observer = Observer()
        handler = SourceChangeHandler(self)
        watched = []
        for d in dirs:
            try:
                d.mkdir(parents=True, exist_ok=True)
                self.observer.schedule(handler, str(d), recursive=True)
                watched.append(str(d))
            except Exception as e:
                logger.warning(f"Failed to watch directory {d}: {e}")
        self.observer.daemon = True
        self.observer.start()
        logger.info(f"LocalSourceWatcher started monitoring: {watched}")

    def stop(self) -> None:
        if self.observer and self.observer.is_alive():
            self.observer.stop()
            self.observer.join(timeout=2.0)
            logger.info("LocalSourceWatcher stopped.")

    def set_sources_dirs(self, dirs) -> None:
        """Update monitored directories (multiple supported). Restarts watcher if running."""
        new_dirs = [Path(d) for d in dirs if str(d).strip()]
        if not new_dirs:
            return
        if new_dirs == self.sources_dirs:
            # Saving other settings must not stop a dispatching Observer.
            # Watchdog.stop() can wait on a lock held by a running file handler.
            return
        self.sources_dirs = new_dirs
        self.sources_dir = new_dirs[0]
        if self.observer and self.observer.is_alive():
            self.stop()
            self.start()

    def handle_file_event(self, file_path: Path, event_type: str = "modified") -> Optional[List[str]]:
        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS or _is_temporary_file(file_path):
            return None

        # Wait for file write completion
        if not _wait_for_file_settle(file_path):
            return None

        try:
            file_hash = artifact_sha256(file_path)
        except Exception:
            return None

        with self._lock:
            # Check if this exact file hash was already processed in this session
            if file_hash in self._processed_hashes:
                return None
            self._processed_hashes.add(file_hash)

        # Shared with cruise and folder scan: skip versions any pipeline already
        # analysed, so one saved file never costs two model calls.
        if not governance.claim_source(file_hash):
            return None
        cids = None
        try:
            cids = self.process_file(file_path, file_hash, event_type)
        finally:
            governance.release_source(file_hash, file_path.name, "watcher",
                                      candidates_created=len(cids) if cids is not None else None)
        try:
            run_project_convergence()
        except Exception as exc:
            logger.warning(f"Project convergence after file event failed: {exc}")
        return cids

    def process_file(self, file_path: Path, file_hash: str, event_type: str = "manual") -> List[str]:
        """Core Pipeline:

        Reader -> Evidence -> Provider -> Candidate
        """
        doc_stem = file_path.stem
        evidence_subdir = self.evidence_dir / f"{doc_stem}_{file_hash[:8]}"
        evidence_subdir.mkdir(parents=True, exist_ok=True)

        created_candidate_ids = []
        try:
            # 1. Run Reader Fidelity Engine
            inp = ReaderInput(
                path=str(file_path),
                source_object_id=doc_stem,
                source_version=file_hash[:12],
                display_name=file_path.name,
                committed=True,
                raw_artifact_sha256=file_hash,
            )
            reader_result = read_artifact(inp, evidence_subdir)
            evidence_json_path = evidence_subdir / "reader_result.json"

            # 2. Extract structured evidence dict
            evidence_data = reader_result.to_dict()
            evidence_data["display_name"] = file_path.name

            # Use the same intake, candidate and source-completion contract as
            # folder scanning and cruise; no second independent proposal loop.
            from backend.runtime_core.planner import agent_planner
            created_candidate_ids = agent_planner.plan_document_impact(
                evidence_data, str(file_path), file_hash)

            record = {
                "file": file_path.name,
                "event_type": event_type,
                "sha256": file_hash,
                "candidates_created": len(created_candidate_ids),
                "timestamp": time.time(),
            }
            self.last_events.insert(0, record)
            if len(self.last_events) > 30:
                self.last_events.pop()

            logger.info(
                f"Processed {file_path.name} -> {len(reader_result.text_blocks)} blocks -> {len(created_candidate_ids)} candidates"
            )
        except Exception as exc:
            logger.error(f"Error processing file {file_path}: {exc}", exc_info=True)
            raise

        return created_candidate_ids

    def scan_now(self) -> Dict[str, Any]:
        """Manually trigger scan of sources directory."""
        results = []
        for scan_dir in (getattr(self, "sources_dirs", None) or [self.sources_dir]):
            if not scan_dir.is_dir():
                continue
            for p in scan_dir.iterdir():
                if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS and not _is_temporary_file(p):
                    cids = self.handle_file_event(p, event_type="batch_scan")
                    if cids is not None:
                        results.append({"file": p.name, "candidates": cids})
        return {"scanned_count": len(results), "items": results}


watcher = LocalSourceWatcher()
