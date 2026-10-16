"""PAW Runtime Scheduler - Manages file cruise, periodic scans, and event triggering.

Supports:
- Real-time file system monitoring
- Periodic cruise (hourly, daily, or user-configured interval)
- Manual on-demand scan
- Full lifecycle telemetry and status metrics
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.core.config import EVIDENCE_DIR, SOURCES_DIR, settings
from backend.governance.service import governance
from backend.reader.contract import ReaderInput, artifact_sha256
from backend.reader.dispatch import read_artifact
from backend.runtime_core.event_bus import EventType, event_bus
from backend.runtime_core.evolution_engine import EvolutionEngine
from backend.runtime_core.planner import agent_planner
from backend.runtime_core.project_analyzer import project_analyzer
from backend.runtime_core.project_convergence import run_project_convergence

logger = logging.getLogger("paw.scheduler")

SUPPORTED_EXTENSIONS = {".docx", ".xlsx", ".xlsm", ".pptx", ".pdf"}


def _is_temporary_file(path: Path) -> bool:
    name = path.name
    return name.startswith("~$") or name.startswith(".") or name.endswith(".tmp")


class PawScheduler:
    def __init__(self, sources_dir: Optional[Path] = None, evidence_dir: Optional[Path] = None) -> None:
        self.sources_dir = Path(sources_dir or SOURCES_DIR)
        self.sources_dirs = [self.sources_dir]
        self.evidence_dir = Path(evidence_dir or EVIDENCE_DIR)
        self.mode: str = "realtime"  # "realtime" | "hourly" | "daily" | "manual"
        self.interval_seconds: int = 3600  # Default 1 hour
        self.last_cruise_at: Optional[str] = None
        self.next_cruise_at: Optional[str] = None
        self.cruise_status: str = "idle"  # "idle" | "running"
        self.total_cruises: int = 0
        self.total_files_analyzed: int = 0
        self.total_candidates_produced: int = 0
        self.latest_cruise_result: Optional[Dict[str, Any]] = None

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._processed_hashes: set[str] = set()
        self._lock = threading.Lock()
        self.evolution_engine = EvolutionEngine()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="PawSchedulerThread", daemon=True)
        self._thread.start()
        logger.info(f"PawScheduler started in mode '{self.mode}'")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("PawScheduler stopped.")

    def set_schedule_mode(self, mode: str, interval_seconds: Optional[int] = None) -> None:
        self.mode = mode
        if interval_seconds:
            self.interval_seconds = interval_seconds
        elif mode in ("hourly", "every_1h"):
            self.interval_seconds = 3600
        elif mode == "every_2h":
            self.interval_seconds = 7200
        elif mode == "every_6h":
            self.interval_seconds = 21600
        elif mode == "daily":
            self.interval_seconds = 86400
        elif mode in ("realtime", "manual"):
            self.interval_seconds = 3600
        logger.info(f"PawScheduler schedule mode updated to '{mode}', interval: {self.interval_seconds}s")

    def _run_loop(self) -> None:
        # Run an initial cruise shortly after startup
        time.sleep(2.0)
        self.run_cruise(trigger="startup")

        while not self._stop_event.is_set():
            if self.mode in ("hourly", "every_1h", "every_2h", "every_6h", "daily"):
                sleep_chunk = 5
                elapsed = 0
                while elapsed < self.interval_seconds and not self._stop_event.is_set():
                    time.sleep(sleep_chunk)
                    elapsed += sleep_chunk
                if not self._stop_event.is_set():
                    self.run_cruise(trigger=self.mode)
            else:
                # Real-time or manual mode sleep
                time.sleep(5.0)

    def run_cruise(self, trigger: str = "manual", force_rescan: bool = False, analyze_projects: bool = True) -> Dict[str, Any]:
        """Execute a complete cruise cycle over working directories and analyze projects with built-in AI."""
        with self._lock:
            if self.cruise_status == "running":
                return {"status": "already_running"}
            self.cruise_status = "running"

        t0 = time.time()
        started_at = datetime.now(timezone.utc).isoformat()
        self.last_cruise_at = started_at
        if self.mode != "manual":
            self.next_cruise_at = datetime.fromtimestamp(
                time.time() + self.interval_seconds, timezone.utc
            ).isoformat()

        if force_rescan:
            self._processed_hashes.clear()

        event_bus.publish(
            EventType.CRUISE_STARTED,
            payload={"trigger": trigger, "started_at": started_at, "sources_dir": str(self.sources_dir)},
            source="scheduler",
        )

        files_processed = 0
        files_attempted = 0
        files_unchanged = 0
        total_files_in_dir = 0
        new_candidates = 0
        item_budget=10  # R5: one-by-one, at most ten intake/reread items in a cruise.

        try:
            for scan_dir in (getattr(self, "sources_dirs", None) or [self.sources_dir]):
                if not scan_dir.is_dir():
                    continue
                for p in scan_dir.iterdir():
                    if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS and not _is_temporary_file(p):
                        if files_attempted>=item_budget:break
                        total_files_in_dir += 1
                        try:
                            f_hash = artifact_sha256(p)
                        except Exception:
                            continue

                        # A file version that any pipeline already analysed is never
                        # sent to the model again, including on a deep cruise:
                        # re-analysis only re-files proposals the user already decided.
                        if f_hash in self._processed_hashes or not governance.claim_source(f_hash):
                            self._processed_hashes.add(f_hash)
                            files_unchanged += 1
                            continue

                        # Process file with fault isolation
                        files_attempted+=1
                        cids = None
                        try:
                            cids = self.process_source_file(p, f_hash)
                            self._processed_hashes.add(f_hash)
                            files_processed += 1
                            if cids:
                                new_candidates += len(cids)
                        except Exception as file_exc:
                            logger.error(f"Error processing source file {p.name}: {file_exc}", exc_info=True)
                        finally:
                            governance.release_source(
                                f_hash, p.name, "cruise",
                                candidates_created=len(cids) if cids is not None else None,
                            )

            # 2. Built-in AI Quick Capture (快速记录) Ingest & Analysis
            captures_processed = 0
            try:
                from backend.workspace_api.repository import unprocessed_captures
                pending_caps = unprocessed_captures(limit=max(0,item_budget-files_attempted))
                for cap in pending_caps:
                    cids = self.process_capture(cap["id"])
                    if cids:
                        new_candidates += len(cids)
                    captures_processed += 1
            except Exception as exc:
                logger.warning(f"Capture cruise processing warning: {exc}")

            # Project convergence: same-name duplicates plus AI grouping of projects that
            # belong to one long-term initiative (model called only when the project list
            # changed, or on a deep cruise); high-confidence groups merge automatically.
            convergence = run_project_convergence(use_ai=True, force_ai=force_rescan)
            new_candidates += len(convergence.get("pending") or [])

            self.total_cruises += 1
            self.total_files_analyzed += files_processed
            self.total_candidates_produced += new_candidates

            # Built-in AI Project Explainer & 30-Second Summary Analysis
            analyzed_projects = []
            if analyze_projects:
                try:
                    analyzed_projects = project_analyzer.analyze_projects(max_projects=max(0,item_budget-files_attempted-captures_processed), force=force_rescan)
                except Exception as exc:
                    logger.warning(f"ProjectAnalyzer during cruise warning: {exc}")

            from backend.pm.reread import run as run_targeted_reread
            remaining=item_budget-files_attempted-captures_processed-len(analyzed_projects)
            reread=run_targeted_reread(remaining) if remaining>0 else {'processed':0,'items':[]}

            # Run Evolution Engine health check and background self-diagnosis
            try:
                evo_diagnosis = self.evolution_engine.run_cruise_evolution_check(self.sources_dir)
            except Exception as e:
                logger.warning(f"Evolution Engine check during cruise warning: {e}")
                evo_diagnosis = {"status": "error", "detail": str(e), "overall_score": 70, "overall_grade": "良好"}

            elapsed_seconds = round(time.time() - t0, 2)
            score = evo_diagnosis.get("overall_score", 0)

            cap_msg = f"，已解析快速记录 {captures_processed} 条" if captures_processed > 0 else ""
            if convergence.get("auto_merged"):
                cap_msg += f"，已自动收敛 {len(convergence['auto_merged'])} 组项目"
            if convergence.get("pending"):
                cap_msg += f"，{len(convergence['pending'])} 组项目合并建议待审核"
            result = {
                "status": "success",
                "trigger": trigger,
                "files_total": total_files_in_dir,
                "files_analyzed": files_processed,
                "files_unchanged": files_unchanged,
                "captures_analyzed": captures_processed,
                "candidates_produced": new_candidates,
                "projects_analyzed": len(analyzed_projects),
                "targeted_reread": reread,
                "projects_details": [p.get("name") for p in analyzed_projects],
                "evolution": evo_diagnosis,
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": elapsed_seconds,
                "summary": (
                    f"巡航完成（耗时 {elapsed_seconds} 秒）：检查工作目录 {total_files_in_dir} 个文件"
                    f"（{files_processed} 个新解析，{files_unchanged} 个最新）{cap_msg}，"
                    f"内置 AI 已完成/更新 {len(analyzed_projects)} 个项目的深度说明与进展提炼，"
                    f"新增 {new_candidates} 条候选待审，知识演进健康度 {score} 分。"
                ),
            }

            self.latest_cruise_result = result
            event_bus.publish(EventType.CRUISE_COMPLETED, payload=result, source="scheduler")
            return result
        finally:
            with self._lock:
                self.cruise_status = "idle"

    def process_capture(self, capture_id: str) -> List[str]:
        """Process a single quick capture record with AI Cruise."""
        import hashlib
        from backend.workspace_api.repository import connect, mark_capture_processed

        with connect() as conn:
            row = conn.execute("SELECT id, raw_text, captured_at, source, processed_at FROM capture WHERE id = ?", (capture_id,)).fetchone()
            if not row:
                return []
            if row["processed_at"] is not None:
                return []
            raw_text = str(row["raw_text"] or "").strip()
            captured_at = str(row["captured_at"] or "")

        if not raw_text:
            mark_capture_processed(capture_id)
            return []

        # 1. Fire SOURCE_READY for capture
        cap_hash = hashlib.sha256((capture_id+'\0'+raw_text).encode("utf-8")).hexdigest()
        if not governance.claim_source(cap_hash):
            if governance.is_source_analyzed(cap_hash):mark_capture_processed(capture_id)
            return []
        event_bus.publish(
            EventType.SOURCE_READY,
            payload={"type": "capture", "capture_id": capture_id, "text_preview": raw_text[:60]},
            source="scheduler_capture",
        )

        # 2. Build structured evidence
        evidence = {
            "display_name": f"快速记录-{captured_at[:10] if captured_at else '便签'}",
            "source_object_id": f"capture:{capture_id}",
            "raw_artifact_sha256": cap_hash,
            "text_blocks": [{"index": 0, "text": raw_text}],
            "is_capture": True,
            "capture_id": capture_id,
        }

        # 3. Fire ANALYSIS_READY
        event_bus.publish(
            EventType.ANALYSIS_READY,
            payload={"type": "capture", "capture_id": capture_id, "sha256": cap_hash},
            source="scheduler_capture",
        )

        # 4. Agent Planner AI proposal
        candidate_ids = None
        try:
            candidate_ids = agent_planner.plan_document_impact(
                evidence=evidence,
                source_file_path=f"capture://{capture_id}",
                file_hash=cap_hash,
            )
            mark_capture_processed(capture_id)
        except Exception as exc:
            logger.error(f"Error analyzing capture {capture_id}: {exc}", exc_info=True)
        finally:
            governance.release_source(cap_hash,evidence['display_name'],'capture',
                candidates_created=len(candidate_ids) if candidate_ids is not None else None)

        # 5. Mark capture as processed
        logger.info(f"Capture {capture_id} cruise analysis finished. Candidates produced: {len(candidate_ids or [])}")
        return candidate_ids or []

    def process_source_file(self, file_path: Path, file_hash: str) -> List[str]:
        """Full pipeline execution:

        File -> Reader -> Evidence -> Agent Planner -> Candidate
        """
        doc_stem = file_path.stem
        evidence_subdir = self.evidence_dir / f"{doc_stem}_{file_hash[:8]}"
        evidence_subdir.mkdir(parents=True, exist_ok=True)

        # 1. Fire SOURCE_READY
        event_bus.publish(
            EventType.SOURCE_READY,
            payload={"file": file_path.name, "path": str(file_path), "sha256": file_hash},
            source="scheduler",
        )

        # 2. Reader Fidelity parsing
        inp = ReaderInput(
            path=str(file_path),
            source_object_id=doc_stem,
            source_version=file_hash[:12],
            display_name=file_path.name,
            committed=True,
            raw_artifact_sha256=file_hash,
        )
        reader_result = read_artifact(inp, evidence_subdir)
        evidence_data = reader_result.to_dict()
        evidence_data["display_name"] = file_path.name

        # 3. Fire ANALYSIS_READY
        event_bus.publish(
            EventType.ANALYSIS_READY,
            payload={
                "file": file_path.name,
                "sha256": file_hash,
                "fingerprint": reader_result.content_fingerprint,
                "text_blocks_count": len(reader_result.text_blocks),
                "tables_count": len(reader_result.tables),
            },
            source="reader_fidelity",
        )

        # 4. Agent Planner & Conflict Analyzer
        candidate_ids = agent_planner.plan_document_impact(
            evidence=evidence_data,
            source_file_path=str(file_path),
            file_hash=file_hash,
        )

        return candidate_ids

    def get_status(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "status": self.cruise_status,
            "last_cruise_at": self.last_cruise_at,
            "next_cruise_at": self.next_cruise_at,
            "interval_seconds": self.interval_seconds,
            "total_cruises": self.total_cruises,
            "total_files_analyzed": self.total_files_analyzed,
            "total_candidates_produced": self.total_candidates_produced,
            "monitored_directory": ";".join(str(d) for d in (getattr(self, "sources_dirs", None) or [self.sources_dir])),
            "files_in_directory": sum(len(list(d.glob("*"))) for d in (getattr(self, "sources_dirs", None) or [self.sources_dir]) if d.is_dir()),
            "latest_cruise_result": self.latest_cruise_result,
        }


scheduler = PawScheduler()
