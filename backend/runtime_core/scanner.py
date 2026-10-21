"""PAW Advanced Local Scanner - Enterprise Work Folder Ingestion & Progression Engine.

Enforces Requirements:
- R1: Ingestion & Analysis Decoupling:
  - Phase 1 (Ingestion): 100% local, 0 model tokens, fast & checkpointable.
  - Phase 2 (Analysis): Priority by mtime desc, daily call quota, folder prioritization, payload preview.
- R2: Subprocess isolation for Reader (hard timeout, memory bound, crash isolation).
- R3: Source identity, SourceVersion grouping (V1/V2/最终版), Temporal Authority (human review required).
- R7: Pre-batch-commit automated database backup.
- 3.2.1 - 3.2.10: Checkpoints, resource throttling, Chinese failed items list, size limits.
- 3.3: Grouped candidate review and 1-page summary.
"""
from __future__ import annotations

import ctypes
import gc
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from backend.core.backup import create_db_backup
from backend.core.config import DATA_DIR, EVIDENCE_DIR, SOURCES_DIR, settings
from backend.governance.service import get_db, governance
from backend.provider import model_calls
from backend.reader.contract import ReaderInput, artifact_sha256
from backend.reader.dispatch import isolated_read_artifact
from backend.runtime_core.event_bus import EventType, event_bus
from backend.runtime_core.planner import agent_planner

logger = logging.getLogger("paw.scanner")

CHECKPOINT_FILE = DATA_DIR / "scan_checkpoint.json"
from backend.core.file_types import SUPPORTED_EXTENSIONS as SUPPORTED_EXTS  # one list for scan, watch and cruise

# Version suffix pattern for multi-version source grouping (R3)
VERSION_SUFFIX_RE = re.compile(
    r"(?:[_\-\s]*(?:v|ver|version)[\d\.]+|[_\-\s]*最终版(?:\(\d+\))?|[_\-\s]*最新版(?:\(\d+\))?|[_\-\s]*初稿|[_\-\s]*修改版(?:\(\d+\))?|[_\-\s]*修订版(?:\(\d+\))?|\(\d+\))$",
    re.IGNORECASE,
)


def _is_temp_or_system_file(path: Path) -> bool:
    name = path.name
    if name.startswith("~$") or name.startswith(".") or name.endswith(".tmp"):
        return True
    if name.lower() in ("thumbs.db", "desktop.ini"):
        return True
    return False


def _safe_str_path(path: Path) -> str:
    """Format path supporting Windows extended-length paths (>260 chars) if needed."""
    p_str = str(path)
    if sys.platform == "win32" and len(p_str) >= 250 and not p_str.startswith("\\\\?\\"):
        return "\\\\?\\" + p_str
    return p_str


def _normalize_source_family(stem: str) -> str:
    """Normalize file stem to group multi-version variants (R3)."""
    clean = stem.strip()
    match = VERSION_SUFFIX_RE.search(clean)
    if match and match.start() > 0:
        base = clean[: match.start()].strip()
        if len(base) >= 2:
            return base
    return clean


class PawScanner:
    """Robust, non-blocking enterprise work folder scanner with checkpoints and throttling."""

    def __init__(self, sources_dir: Optional[Path] = None, evidence_dir: Optional[Path] = None) -> None:
        _wd = str(settings.work_dir or "").split(";")[0].strip()
        self.sources_dir = Path(sources_dir or (_wd or SOURCES_DIR))
        self.sources_dirs: List[Path] = [self.sources_dir]
        self.evidence_dir = Path(evidence_dir or EVIDENCE_DIR)

        # State machine: "idle" | "prescanning" | "phase1_ingesting" | "phase2_analyzing" | "paused" | "stopped" | "completed" | "daily_limit_reached"
        self.status: str = "idle"
        self.current_phase: str = "idle"
        self.current_file: str = ""
        self.total_files: int = 0
        self.phase1_processed: int = 0
        self.phase1_success: int = 0
        self.phase2_processed: int = 0
        self.phase2_success: int = 0
        self.failed_files: int = 0
        self.new_candidates_count: int = 0

        self.start_time: float = 0.0
        self.elapsed_seconds: int = 0
        self.estimated_remaining_seconds: int = 0

        self.priority_folder: Optional[str] = None
        self.failed_items: List[Dict[str, Any]] = []
        self._checkpoint: Dict[str, Dict[str, Any]] = {}

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._lock = threading.Lock()

        self._load_checkpoint()

    # ── Checkpoint Persistence (Requirement 3.2.3) ───────────────────────────

    def _load_checkpoint(self) -> None:
        if CHECKPOINT_FILE.is_file():
            try:
                self._checkpoint = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"Failed to load checkpoint: {e}")
                self._checkpoint = {}

    def _save_checkpoint(self) -> None:
        try:
            CHECKPOINT_FILE.write_text(
                json.dumps(self._checkpoint, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Failed to save checkpoint: {e}")

    # ── Pre-Scan Assessment (Requirement 3.2.9 & R1.1) ───────────────────────

    def prescan(self, target_dir: Optional[Path] = None) -> Dict[str, Any]:
        """Rapid pre-scan estimating Phase 1 & Phase 2 time and model call quota."""
        if target_dir:
            dirs_to_scan = [Path(target_dir)]
        else:
            dirs_to_scan = list(getattr(self, "sources_dirs", None) or [self.sources_dir])
        dirs_to_scan = [d for d in dirs_to_scan if d.is_dir()]
        if not dirs_to_scan:
            return {
                "ok": False,
                "message": f"目录不存在: {self.sources_dir}",
                "total_files": 0,
                "estimated_minutes": 0,
            }

        valid_files = []
        skipped_temp_count = 0
        oversized_count = 0
        max_bytes = settings.max_file_size_mb * 1024 * 1024

        try:
            for scan_dir in dirs_to_scan:
                for root, dirs, files in os.walk(_safe_str_path(scan_dir)):
                    dirs[:] = [d for d in dirs if d not in settings.excluded_dirs and not d.startswith(".")]
                    for fname in files:
                        fpath = Path(root) / fname
                        if _is_temp_or_system_file(fpath):
                            skipped_temp_count += 1
                            continue
                        if fpath.suffix.lower() in SUPPORTED_EXTS:
                            try:
                                fsize = fpath.stat().st_size
                                if fsize > max_bytes:
                                    oversized_count += 1
                                else:
                                    valid_files.append((str(fpath), fsize))
                            except OSError:
                                continue
        except Exception as e:
            return {"ok": False, "message": f"预扫描出错: {str(e)}", "total_files": 0}

        count = len(valid_files)
        # Phase 1: fast local parsing (~0.5s per file)
        est_seconds_p1 = int(count * 0.5)
        est_minutes_p1 = round(est_seconds_p1 / 60, 1) if est_seconds_p1 > 60 else 1

        return {
            "ok": True,
            "directory": str(dir_to_scan),
            "total_files": count,
            "skipped_temp_files": skipped_temp_count,
            "oversized_files": oversized_count,
            "phase1_estimated_minutes": est_minutes_p1,
            "estimated_minutes": est_minutes_p1,
            "phase2_estimated_calls": count,
            "daily_call_limit": settings.daily_call_limit,
            "today_calls_made": settings.today_calls_made,
            "message": (
                f"共发现 {count} 个可解析办公文档（已过滤临时文件 {skipped_temp_count} 个）。\n"
                f"- 阶段一：本地摄取与证据提取预计耗时约 {est_minutes_p1} 分钟（100% 离线运行，不消耗任何模型额度）；\n"
                f"- 阶段二：若全部分析，预计最多需要 {count} 次模型调用"
                + (f"（当前每日上限 {settings.daily_call_limit} 次，超出部分留在队列明日继续）。" if settings.daily_call_limit > 0 else "（当前配置为无上限，将连续完整分析全部队列）。")
            ),
        }

    # ── Scan Execution (R1 Decoupled: Phase 1 Ingestion + Phase 2 Analysis) ────

    def start_scan(
        self,
        target_dir: Optional[Path] = None,
        force_restart: bool = False,
        priority_folder: Optional[str] = None,
        phase: str = "all",
    ) -> Dict[str, Any]:
        """Start scanning. If phase='phase1', runs only local ingestion; if 'all', runs Phase 1 then Phase 2."""
        with self._lock:
            if self.status in ("phase1_ingesting", "phase2_analyzing"):
                return {"ok": False, "message": "扫描已在运行中，请勿重复发起"}
            if self.status == "paused":
                self.resume_scan()
                return {"ok": True, "message": "已从暂停状态继续恢复扫描"}

            if target_dir:
                self.sources_dir = Path(target_dir)
                self.sources_dirs = [self.sources_dir]
            if priority_folder:
                self.priority_folder = str(priority_folder)

            if force_restart:
                self._checkpoint.clear()
                self._save_checkpoint()

            self.status = "phase1_ingesting"
            self.current_phase = "phase1"
            self._stop_event.clear()
            self._pause_event.set()
            self.failed_items.clear()
            self.phase1_processed = 0
            self.phase1_success = 0
            self.phase2_processed = 0
            self.phase2_success = 0
            self.failed_files = 0
            self.new_candidates_count = 0
            self.start_time = time.time()
            # Per-call model telemetry belongs to this run only.
            model_calls.reset()

            self._thread = threading.Thread(
                target=self._run_scan_thread,
                args=(phase,),
                name="PawScannerWorker",
                daemon=True,
            )
            self._thread.start()

            return {"ok": True, "message": "扫描任务已在后台启动（阶段一：本地摄取启动）"}

    def pause_scan(self) -> Dict[str, Any]:
        with self._lock:
            if self.status not in ("phase1_ingesting", "phase2_analyzing", "scanning"):
                return {"ok": False, "message": f"当前状态不是正在扫描 (当前: {self.status})"}
            self.status = "paused"
            self._pause_event.clear()
            gc.collect()
            logger.info("PawScanner paused. CPU cycles and memory released.")
            return {"ok": True, "message": "扫描已暂停，系统资源已释放"}

    def resume_scan(self) -> Dict[str, Any]:
        with self._lock:
            if self.status != "paused":
                return {"ok": False, "message": "当前并非处于暂停状态"}
            self.status = "scanning" if self.current_phase in ("idle", "scanning") else self.current_phase
            self._pause_event.set()
            logger.info("PawScanner resumed.")
            return {"ok": True, "message": "扫描已继续"}

    def stop_scan(self) -> Dict[str, Any]:
        with self._lock:
            if self.status not in ("phase1_ingesting", "phase2_analyzing", "scanning", "paused"):
                return {"ok": False, "message": "当前没有正在进行的扫描任务"}
            self.status = "stopped"
            self._stop_event.set()
            self._pause_event.set()
            logger.info("PawScanner stop signal sent.")
            return {"ok": True, "message": "正在停止扫描，当前文件处理完毕后将退出并保留断点"}

    def _set_thread_throttle(self) -> None:
        if sys.platform == "win32":
            try:
                priority = -1 if settings.speed_mode == "quiet" else 0
                handle = ctypes.windll.kernel32.GetCurrentThread()
                ctypes.windll.kernel32.SetThreadPriority(handle, priority)
            except Exception:
                pass

    def _run_scan_thread(self, target_phase: str = "all") -> None:
        try:
            self._run_scan_phases(target_phase)
            from backend.pm.health import observe
            if self.status=='completed':observe('scan',self.failed_files==0,'扫描完成' if self.failed_files==0 else '扫描结束但部分文件未能读取')
        except Exception:
            self.status = 'failed'
            from backend.pm.health import observe
            observe('scan',False,'扫描失败，保留检查点等待重试')
            self.current_phase = 'failed'
            logger.exception('扫描未完成，保留检查点以便重试')
            self._save_checkpoint()

    def _run_scan_phases(self, target_phase: str = "all") -> None:
        self._set_thread_throttle()
        logger.info(f"PawScannerWorker thread started on directory: {self.sources_dir}")

        event_bus.publish(
            EventType.CRUISE_STARTED,
            payload={"trigger": "full_folder_scan", "sources_dir": str(self.sources_dir)},
            source="scanner",
        )

        all_file_paths: List[Path] = []
        max_bytes = settings.max_file_size_mb * 1024 * 1024
        self._effective_max_mb = settings.max_file_size_mb

        # 1. Discover all candidate files (multiple monitored directories supported)
        for scan_dir in list(getattr(self, "sources_dirs", None) or [self.sources_dir]):
            if self._stop_event.is_set():
                break
            for root, dirs, files in os.walk(_safe_str_path(scan_dir)):
                if self._stop_event.is_set():
                    break
                dirs[:] = [d for d in dirs if d not in settings.excluded_dirs and not d.startswith(".")]
                for fname in files:
                    fpath = Path(root) / fname
                    if _is_temp_or_system_file(fpath):
                        continue
                    if fpath.suffix.lower() in SUPPORTED_EXTS:
                        all_file_paths.append(fpath)

        self.total_files = len(all_file_paths)
        logger.info(f"Discovered {self.total_files} candidate files to parse.")

        # ── PHASE 1: INGESTION (100% Local, 0 Model Tokens, Subprocess Isolated) ──
        if target_phase != "phase2":
            self.status = "phase1_ingesting"
            self.current_phase = "phase1_ingesting"

            for p in all_file_paths:
                if self._stop_event.is_set():
                    break
                if not self._pause_event.is_set():
                    self._pause_event.wait()
                    if self._stop_event.is_set():
                        break

                self.current_file = p.name
                file_key = str(p.resolve())

                try:
                    stat = p.stat()
                    mtime = stat.st_mtime
                    size = stat.st_size
                except OSError as e:
                    self._record_failure(p, "PERM_ERROR", f"无法读取文件元数据: {str(e)}", "请检查文件访问权限")
                    self.phase1_processed += 1
                    continue

                if size > max_bytes:
                    size_mb = round(size / (1024 * 1024), 1)
                    self._record_failure(
                        p,
                        "OVERSIZED",
                        f"文件大小 ({size_mb} MB) 超过本次扫描生效的 {getattr(self, '_effective_max_mb', settings.max_file_size_mb)} MB 上限（以扫描开始时的设置为准，修改设置后重新扫描即按新值生效）",
                        "可在设置中增大单文件限制或对大文档进行拆分",
                    )
                    self.phase1_processed += 1
                    continue

                # Checkpoint check for Phase 1
                cp_entry = self._checkpoint.get(file_key)
                if (
                    cp_entry
                    and cp_entry.get("mtime") == mtime
                    and cp_entry.get("size") == size
                    and cp_entry.get("phase1_status") == "success"
                ):
                    # Populate the source registry for files scanned by older
                    # versions too. Registration is keyed by canonical path,
                    # so rescans update the existing source instead of adding
                    # another row.
                    checkpoint_hash = cp_entry.get("hash")
                    if checkpoint_hash:
                        self._register_local_scan_source(p, checkpoint_hash, mtime, size)
                    self.phase1_processed += 1
                    self.phase1_success += 1
                    continue

                # Execute Phase 1 single file
                success = self._process_phase1_file(p, file_key, mtime, size)
                self.phase1_processed += 1
                if success:
                    self.phase1_success += 1
                else:
                    self.failed_files += 1

                self.elapsed_seconds = int(time.time() - self.start_time)
                if self.phase1_processed > 0:
                    avg_time = self.elapsed_seconds / self.phase1_processed
                    self.estimated_remaining_seconds = int((self.total_files - self.phase1_processed) * avg_time)

                if settings.speed_mode == "quiet":
                    time.sleep(0.1)

            self._save_checkpoint()
            if self._stop_event.is_set() or target_phase == "phase1":
                self.current_file = ""
                self.status = "stopped" if self._stop_event.is_set() else "completed"
                return

        # ── PHASE 2: ANALYSIS (AI Inference, Priority by mtime desc, Quota Bounded) ──
        self.status = "phase2_analyzing"
        self.current_phase = "phase2_analyzing"

        # Sort candidate files by mtime DESC (R1.2) with priority_folder first (R1.4)
        def _sort_key(p: Path) -> Tuple[int, float]:
            is_priority = 1 if self.priority_folder and self.priority_folder.lower() in str(p).lower() else 0
            try:
                mt = p.stat().st_mtime
            except OSError:
                mt = 0.0
            return (is_priority, mt)

        sorted_files = sorted(all_file_paths, key=_sort_key, reverse=True)

        for p in sorted_files:
            if self._stop_event.is_set():
                break
            if not self._pause_event.is_set():
                self._pause_event.wait()
                if self._stop_event.is_set():
                    break

            file_key = str(p.resolve())
            cp_entry = self._checkpoint.get(file_key)
            if not cp_entry or cp_entry.get("phase1_status") != "success":
                continue  # Skip files that failed Phase 1

            if cp_entry.get("phase2_status") == "success":
                self.phase2_processed += 1
                self.phase2_success += 1
                continue

            # Already analysed by any pipeline: skip before spending a daily call.
            # A watcher/cruise can own this version while the scanner runs.
            # Completion must include that owner, not just this worker's loop.
            governance.wait_for_source_completion(cp_entry.get('hash', ''))
            if governance.is_source_analyzed(cp_entry.get("hash", "")):
                cp_entry["phase2_status"] = "skipped_already_analyzed"
                self.phase2_processed += 1
                continue

            # Check daily call limit (R1.2, R1.3)
            if not settings.check_and_increment_daily_call():
                remaining_analysis = len(all_file_paths) - self.phase2_processed
                self.status = "daily_limit_reached"
                logger.info(
                    f"Phase 2 daily call limit reached ({settings.daily_call_limit}). "
                    f"Remaining {remaining_analysis} items queued for tomorrow."
                )
                break

            self.current_file = p.name
            evidence_file = cp_entry.get("evidence_result_path")
            f_hash = cp_entry.get("hash", "")

            success = self._process_phase2_file(p, file_key, evidence_file, f_hash)
            self.phase2_processed += 1
            if success:
                self.phase2_success += 1
            # Persist checkpoint immediately so an interrupted run resumes
            # from the exact file where it stopped (not from phase start).
            self._save_checkpoint()

        self.current_file = ""
        self.estimated_remaining_seconds = 0
        if not self._stop_event.is_set() and self.status != "daily_limit_reached":
            self.status = "completed"

        self._save_checkpoint()
        event_bus.publish(
            EventType.CRUISE_COMPLETED,
            payload={
                "status": self.status,
                "total_files": self.total_files,
                "phase1_success": self.phase1_success,
                "phase2_success": self.phase2_success,
                "failed_files": self.failed_files,
                "candidates_created": self.new_candidates_count,
            },
            source="scanner",
        )

    # ── Phase 1 Worker: Subprocess Isolation & Source Identity ─────────────────

    def _process_single_file(self, p: Path, file_key: str, mtime: float, size: int) -> bool:
        """Compatibility wrapper for single file ingestion."""
        return self._process_phase1_file(p, file_key, mtime, size)

    def _process_phase1_file(self, p: Path, file_key: str, mtime: float, size: int) -> bool:
        """Run Phase 1: Subprocess-isolated Reader + Source / SourceVersion registration."""
        try:
            f_hash = artifact_sha256(p)
        except PermissionError:
            self._record_failure(p, "LOCKED", "文档当前被其他软件（如 Word / Excel）独占打开", "请保存并关闭相关软件后点击重试")
            return False
        except Exception as e:
            self._record_failure(p, "READ_ERROR", f"无法读取文件哈希: {str(e)}", "检查文件是否损坏或存在特殊权限限制")
            return False

        doc_stem = p.stem
        evidence_subdir = self.evidence_dir / f"{doc_stem}_{f_hash[:8]}"
        evidence_subdir.mkdir(parents=True, exist_ok=True)

        inp = ReaderInput(
            path=str(p),
            source_object_id=doc_stem,
            source_version=f_hash[:12],
            display_name=p.name,
            committed=True,
            raw_artifact_sha256=f_hash,
        )

        # R2: Subprocess isolation with watchdog
        ok, reader_result, fail_code, fail_reason = isolated_read_artifact(inp, evidence_subdir, timeout=60)
        if not ok or not reader_result:
            from backend.pm import storage
            from backend.pm.coverage import record
            with storage.connect() as conn:
                record(conn,f_hash,p.name,reader_result.to_dict() if reader_result else {
                    'reader_type':p.suffix.lstrip('.').upper(),'warnings':['资料读取失败：'+(fail_code or 'READ_FAILED')],
                    'fidelity_flags':{'structure_parsed':False}})
            self._record_failure(
                p,
                fail_code or "PARSE_ERROR",
                fail_reason or "高保真逆向提取失败",
                "请检查文档是否损坏、加密或格式特殊",
            )
            return False

        evidence_result_path = evidence_subdir / "reader_result.json"

        # R3: Register Source Identity & SourceVersion in workspace.db
        self._register_source_identity_and_version(p, doc_stem, f_hash, mtime, size)

        self._checkpoint[file_key] = {
            "hash": f_hash,
            "mtime": mtime,
            "size": size,
            "phase1_status": "success",
            "phase2_status": "pending",
            "evidence_result_path": str(evidence_result_path),
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        return True

    def _register_source_identity_and_version(self, p: Path, doc_stem: str, f_hash: str, mtime: float, size: int) -> None:
        """Register immutable intake only; PM Committer owns effective family/status."""
        now = datetime.now(timezone.utc).isoformat()
        sid = f_hash[:16]
        conn = get_db()
        try:
            with conn:
                logical_id = "intake-" + sid
                conn.execute("INSERT OR IGNORE INTO logical_document VALUES(?,?,?,'active',?,?)",
                             (logical_id,f_hash,p.name,now,now))
                conn.execute("""INSERT OR IGNORE INTO source_version_governance(
                    source_version_id,logical_document_id,governance_status,authority_rank,
                    authority_reason,confidence,provenance_json,created_at,updated_at)
                    VALUES(?,?,'unresolved',99,'摄取登记；有效版本由 PM 治理链裁定','HIGH',?,?,?)""",
                    (sid,logical_id,json.dumps({'file_name':p.name,'hash':f_hash},ensure_ascii=False),now,now))
        finally:
            conn.close()
        self._register_local_scan_source(p, f_hash, mtime, size)

    @staticmethod
    def _register_local_scan_source(p: Path, f_hash: str, mtime: float, size: int) -> None:
        """Expose scanned files in the source library without creating Knowledge."""
        from backend.workspace_api import repository

        kind_by_ext = {
            ".docx": "document",
            ".txt": "document",
            ".text": "document",
            ".md": "document",
            ".pdf": "pdf",
            ".xlsx": "spreadsheet",
            ".xlsm": "spreadsheet",
            ".pptx": "presentation",
        }
        path = p.resolve()
        repository.register_source(
            title=p.name,
            canonical_location=_safe_str_path(path),
            source_origin="local_scan",
            content_hash=f_hash,
            file_ext=p.suffix,
            source_kind=kind_by_ext[p.suffix.lower()],
            canonical_state="canonical",
            size=size,
            mtime=datetime.fromtimestamp(mtime, timezone.utc).isoformat(),
        )

    # ── Phase 2 Worker: AI Analysis & Candidate Proposal ──────────────────────

    def _process_phase2_file(self, p: Path, file_key: str, evidence_file: Optional[str], f_hash: str) -> bool:
        """Run Phase 2: AI model analyzes Evidence to produce Candidates (strictly bounded)."""
        if not evidence_file or not Path(evidence_file).is_file():
            return False

        try:
            evidence_data = json.loads(Path(evidence_file).read_text(encoding="utf-8"))
            evidence_data["display_name"] = p.name
        except Exception as e:
            logger.warning(f"Failed to load evidence for {p.name}: {e}")
            return False

        # Shared with watcher and cruise: an already-analysed file version is not
        # sent to the model again (saves quota and avoids re-filing decided proposals).
        if not governance.claim_source(f_hash):
            governance.wait_for_source_completion(f_hash)
            if not governance.is_source_analyzed(f_hash):
                raise RuntimeError('其他管线未完成资料分析；本次扫描不能标记为完成')
            cp_entry = self._checkpoint.get(file_key, {})
            cp_entry["phase2_status"] = "skipped_already_analyzed"
            self._checkpoint[file_key] = cp_entry
            return False

        cids = None
        try:
            cids = agent_planner.plan_document_impact(evidence_data, str(p), f_hash)
            self.new_candidates_count += len(cids)
            cp_entry = self._checkpoint.get(file_key, {})
            cp_entry["phase2_status"] = "success"
            cp_entry["candidates_created"] = len(cids)
            cp_entry["analyzed_at"] = datetime.now(timezone.utc).isoformat()
            self._checkpoint[file_key] = cp_entry
            return True
        except Exception as err:
            logger.warning(f"AgentPlanner evaluation failed on {p.name}: {err}")
            return False
        finally:
            governance.release_source(f_hash, p.name, "scan",
                                      candidates_created=len(cids) if cids is not None else None)

    # ── Payload Preview (Requirement R1.5) ───────────────────────────────────

    def preview_payload(self, file_path_str: str) -> Dict[str, Any]:
        """Preview exact prompt and context payload before sending to model."""
        p = Path(file_path_str)
        file_key = str(p.resolve())
        cp = self._checkpoint.get(file_key)
        if not cp or not cp.get("evidence_result_path"):
            return {
                "ok": False,
                "message": "该文件尚未完成阶段一摄取，暂无可用证据载荷",
                "payload": {},
            }

        ev_path = Path(cp["evidence_result_path"])
        if not ev_path.is_file():
            return {"ok": False, "message": "证据文件不存在", "payload": {}}

        try:
            ev_data = json.loads(ev_path.read_text(encoding="utf-8"))
            text_samples = [
                f"[{b.get('index')}] {b.get('text', '')[:120]}"
                for b in ev_data.get("text_blocks", [])[:10]
            ]
            return {
                "ok": True,
                "file_path": str(p),
                "file_name": p.name,
                "file_hash": cp.get("hash"),
                "text_block_count": len(ev_data.get("text_blocks", [])),
                "table_count": len(ev_data.get("tables", [])),
                "preview_text_samples": text_samples,
                "model_context_summary": f"仅发送文档结构与提取的必要文本切片（共 {len(text_samples)} 个样本），不发送文件整库。",
            }
        except Exception as e:
            return {"ok": False, "message": str(e), "payload": {}}

    # ── Failure Tracking & Single File Retry ─────────────────────────────────

    def _record_failure(self, p: Path, error_type: str, message: str, suggestion: str) -> None:
        file_key = str(p.resolve())
        item = {
            "file_path": str(p),
            "file_name": p.name,
            "error_type": error_type,
            "error_message": message,
            "reason": message,
            "suggestion": suggestion,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.failed_items.append(item)
        self._checkpoint[file_key] = {
            "status": "failed",
            "phase1_status": "failed",
            "error_type": error_type,
            "error_message": message,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.warning(f"Scanner recorded failure: {p.name} [{error_type}] -> {message}")

    def retry_failed_file(self, file_path_str: str) -> Dict[str, Any]:
        p = Path(file_path_str)
        if not p.is_file():
            return {"ok": False, "message": "文件不存在或已被移除"}

        file_key = str(p.resolve())
        stat = p.stat()
        success = self._process_phase1_file(p, file_key, stat.st_mtime, stat.st_size)
        if success:
            self.failed_items = [it for it in self.failed_items if it["file_path"] != file_path_str]
            self._save_checkpoint()
            return {"ok": True, "message": "重试解析成功！已更新检查点"}
        else:
            return {"ok": False, "message": "重试解析仍未成功，请参阅失败原因"}

    # ── Progress & Grouped Candidates ────────────────────────────────────────

    def get_progress(self) -> Dict[str, Any]:
        # Resumable checkpoint stats: files already processed in a previous
        # (possibly interrupted) scan run. Persisted on disk, survives restarts.
        resumable = 0
        try:
            for entry in self._checkpoint.values():
                if entry.get("phase1_status") == "success":
                    resumable += 1
        except Exception:
            pass
        elapsed = self.elapsed_seconds
        try:
            _st = getattr(self, "start_time", None)
            if _st and self.status in ("phase1_ingesting", "phase2_analyzing"):
                elapsed = int(time.time() - _st)
        except Exception:
            pass
        eta = self.estimated_remaining_seconds
        if self.status == "phase2_analyzing" and self.phase2_processed > 0 and self.total_files > self.phase2_processed:
            try:
                eta = int((elapsed / self.phase2_processed) * (self.total_files - self.phase2_processed))
            except Exception:
                pass
        return {
            "status": self.status,
            "current_phase": self.current_phase,
            "total_files": self.total_files,
            "phase1_processed": self.phase1_processed,
            "phase1_success": self.phase1_success,
            "phase2_processed": self.phase2_processed,
            "phase2_success": self.phase2_success,
            "failed_files": len(self.failed_items),
            "current_file": self.current_file,
            "elapsed_seconds": elapsed,
            "estimated_remaining_seconds": eta,
            "speed_mode": settings.speed_mode,
            "daily_call_limit": settings.daily_call_limit,
            "today_calls_made": settings.today_calls_made,
            "new_candidates_count": self.new_candidates_count,
            "failed_items": self.failed_items,
            "resumable_files": resumable,
            # Phase 2 model-call detail: file, source, attempt, seconds, status,
            # timeout, prompt characters, usage tokens, rule-fallback flag.
            "model_calls": model_calls.snapshot("scan"),
            "model_call_summary": model_calls.summary("scan"),
        }

    def get_grouped_candidates(self) -> Dict[str, Any]:
        conn = get_db()
        try:
            cur = conn.execute(
                """SELECT c.id, c.candidate_type, c.confidence, c.created_at, c.analysis_model,
                          c.raw_excerpt, c.proposed_change, c.review_brief_json, c.review_internal_evidence_json,
                          c.status, p.name as aligned_project_name
                   FROM candidate c
                   LEFT JOIN project p ON c.applied_entity_type = 'project' AND c.applied_entity_id = p.id
                   WHERE c.status = 'pending'
                   ORDER BY c.created_at DESC LIMIT 200"""
            )
            rows = cur.fetchall()
            true_total = conn.execute("SELECT count(*) FROM candidate WHERE status = 'pending'").fetchone()[0]

            from collections import defaultdict
            groups_by_project = defaultdict(list)
            for r in rows:
                p_name = r["aligned_project_name"] or "未归属特定项目"
                brief = {}
                try:
                    brief = json.loads(r["review_brief_json"]) if r["review_brief_json"] else {}
                except Exception:
                    pass

                change_payload = {}
                try:
                    change_payload = json.loads(r["proposed_change"]) if r["proposed_change"] else {}
                except Exception:
                    pass

                raw_ex = r["raw_excerpt"] or ""
                title = change_payload.get("payload", {}).get("title") or brief.get("summary") or raw_ex[:40] or "待审提案"

                groups_by_project[p_name].append({
                    "id": r["id"],
                    "candidate_type": r["candidate_type"],
                    "confidence": r["confidence"],
                    "title": title,
                    "model": r["analysis_model"],
                    "is_ai": not r["analysis_model"].startswith("rule:"),
                    "rule_fallback_notice": brief.get("rule_fallback_notice", ""),
                    "summary": brief.get("summary", ""),
                    "created_at": r["created_at"],
                })

            return {
                "total_pending": true_total,
                "loaded_count": len(rows),
                "total_groups": len(groups_by_project),
                "groups": [
                    {"group_name": k, "items": v, "count": len(v)}
                    for k, v in groups_by_project.items()
                ],
            }
        finally:
            conn.close()

    def batch_accept(self, candidate_ids: List[str], reviewer: str = "LocalUser") -> Dict[str, Any]:
        """Requirement 3.3.4 & R7.2: Pre-batch-commit auto backup then batch commit."""
        if candidate_ids:
            try:
                create_db_backup(tag="pre_batch_commit")
            except Exception as e:
                logger.warning(f"Pre-batch-commit backup warning: {e}")

        accepted = []
        failed = []
        for cid in candidate_ids:
            try:
                res = governance.accept_and_commit_candidate(cid, reviewer=reviewer, review_notes="用户批量核准写入")
                accepted.append(cid)
            except Exception as e:
                failed.append({"id": cid, "error": str(e)})

        logger.info(f"Batch accept complete: {len(accepted)} accepted, {len(failed)} failed.")
        return {
            "ok": True,
            "accepted_count": len(accepted),
            "commit_records_created": len(accepted),
            "failed_count": len(failed),
            "accepted_ids": accepted,
            "failed_details": failed,
        }

    def batch_reject(self, candidate_ids: List[str], reviewer: str = "LocalUser", notes: str = "") -> Dict[str, Any]:
        rejected = []
        failed = []
        for cid in candidate_ids:
            try:
                governance.reject_candidate(cid, reviewer=reviewer, notes=notes or "用户批量驳回")
                rejected.append(cid)
            except Exception as e:
                failed.append({"id": cid, "error": str(e)})

        return {
            "ok": True,
            "rejected_count": len(rejected),
            "failed_count": len(failed),
            "rejected_ids": rejected,
        }

    def get_scan_summary(self) -> Dict[str, Any]:
        conn = get_db()
        try:
            total_pending = conn.execute("SELECT count(*) FROM candidate WHERE status = 'pending'").fetchone()[0]
            know_count = conn.execute("SELECT count(*) FROM candidate WHERE status = 'pending' AND candidate_type = 'knowledge'").fetchone()[0]
            todo_count = conn.execute("SELECT count(*) FROM candidate WHERE status = 'pending' AND candidate_type = 'todo'").fetchone()[0]
            metric_count = conn.execute("SELECT count(*) FROM candidate WHERE status = 'pending' AND candidate_type = 'data'").fetchone()[0]
            high_conf = conn.execute("SELECT count(*) FROM candidate WHERE status = 'pending' AND confidence = 'HIGH'").fetchone()[0]

            return {
                "total_pending_candidates": total_pending,
                "knowledge_candidates": know_count,
                "todo_candidates": todo_count,
                "metric_candidates": metric_count,
                "high_confidence_candidates": high_conf,
                "recommended_for_initial_review": min(high_conf, 15),
                "summary_text": (
                    f"首轮扫描已完成！共为您提炼出 {total_pending} 项待核准提案（含 {know_count} 条知识沉淀、"
                    f"{todo_count} 项行动待办、{metric_count} 项量化指标）。"
                    f"其中 {high_conf} 项为高置信度推荐，建议您先进入审核中心确认前 {min(high_conf, 15)} 条高价值条目。"
                ),
            }
        finally:
            conn.close()


scanner = PawScanner()


def scan_folder(target_dir: str | Path, phase: int = 1) -> Dict[str, Any]:
    """Synchronous scanner runner for automated tests and CLI scripting."""
    sc = PawScanner(sources_dir=Path(target_dir))
    target_phase = "phase1" if phase == 1 else "phase2" if phase == 2 else "all"
    sc.start_time = time.time()
    sc._run_scan_thread(target_phase=target_phase)
    return {
        "status": sc.status,
        "total_files": sc.total_files,
        "phase1_files_ingested": sc.phase1_success,
        "phase2_analyzed": sc.phase2_success,
        "phase2_queue_remaining": sc.total_files - sc.phase2_processed,
        "failed_files": sc.failed_files,
    }
