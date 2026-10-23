"""Diagnostics for the Phase 2 (model analysis) stage of a folder scan.

The acceptance runs showed a scan sitting in ``phase2_analyzing`` for longer than
the harness waits, while the HTTP side of the same process answered normally.
That means one worker thread was waiting on something and nothing recorded what.
This module records the wait points the scan passes through and, when a file
makes no progress for ten seconds, dumps every thread's stack through
``faulthandler`` so the next occurrence answers the question instead of
prompting another guess.

Nothing here records prompts, file contents, request bodies or credentials:
events carry names, hashes prefixes, counts and durations only.
"""
from __future__ import annotations

import faulthandler
import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("paw.scan.diag")

STALL_SECONDS = 10.0
DUMP_EVERY_SECONDS = 20.0
MAX_DUMPS = 5


def short(value: Any, limit: int = 16) -> str:
    """A hash or id abbreviated for a log line."""
    text = str(value or "")
    return text[:limit] if len(text) > limit else text


def note(event: str, **fields: Any) -> str:
    """One structured diagnostic line (no prompts, no credentials)."""
    parts = [f"{key}={value}" for key, value in fields.items() if value is not None]
    message = f"scan {event}" + (" " + " ".join(parts) if parts else "")
    logger.info(message)
    return message


def _dump_path() -> Optional[str]:
    try:
        from backend.core.log_setup import ensure_file_logging
        return str(ensure_file_logging())
    except Exception:
        return None


def write_state_snapshot(scanner: Any, state: Dict[str, Any]) -> Optional[str]:
    """Write the scan state next to the mirrored log when a stall is detected.

    The acceptance cleanup deletes %LOCALAPPDATA%\\PAW between cases, so the
    checkpoint alone does not survive to the end of the job; this copy does.
    Names, hashes prefixes and durations only.
    """
    try:
        import json
        from backend.core.log_setup import mirror_dir
        directory = mirror_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "scan_state.json"
        payload = {
            "status": getattr(scanner, "status", ""),
            "current_file": getattr(scanner, "current_file", ""),
            "phase2_processed": getattr(scanner, "phase2_processed", None),
            "phase2_success": getattr(scanner, "phase2_success", None),
            "failed_files": getattr(scanner, "failed_files", None),
            "diagnostics": dict(state or {}),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)
    except Exception:
        return None


class Phase2Watchdog(threading.Thread):
    """Dump all thread stacks when one Phase 2 file stops making progress."""

    def __init__(self, scanner: Any, stall_seconds: float = STALL_SECONDS) -> None:
        super().__init__(name="PawScanWatchdog", daemon=True)
        self.scanner = scanner
        self.stall_seconds = float(stall_seconds)
        self._stop = threading.Event()
        self._dumps = 0
        self._last_dump_at = 0.0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:  # pragma: no cover - exercised through check_once
        while not self._stop.wait(1.0):
            try:
                self.check_once()
            except Exception:
                pass

    def check_once(self, now: Optional[float] = None) -> bool:
        """Dump once if the current file has been idle past the stall window."""
        now = time.monotonic() if now is None else now
        snapshot = getattr(self.scanner, "phase2_diagnostics", None)
        if not callable(snapshot):
            return False
        state = snapshot() or {}
        started_at = float(state.get("progress_at") or 0.0)
        if not state.get("active") or not started_at:
            return False
        idle = now - started_at
        if idle < self.stall_seconds:
            return False
        if self._dumps >= MAX_DUMPS or (now - self._last_dump_at) < DUMP_EVERY_SECONDS:
            return False
        self._dumps += 1
        self._last_dump_at = now
        stage = state.get("stage") or "unknown"
        fields = dict(
            file=state.get("file") or "-",
            hash=short(state.get("hash")),
            stage=stage,
            idle_seconds=round(idle, 1),
            progress_seconds=round(float(state.get("file_seconds") or 0.0), 1),
            dump=self._dumps,
        )
        logger.warning(
            "scan watchdog no_progress "
            + " ".join(f"{key}={value}" for key, value in fields.items())
        )
        self.dump_stacks(stage, fields)
        try:
            write_state_snapshot(self.scanner, state)
        except Exception:
            pass
        try:
            from backend.pm import health
            health.observe("scan", False,
                           f"扫描在 {state.get('file') or '某个文件'} 停住 {int(idle)} 秒")
        except Exception:
            pass
        return True

    def dump_stacks(self, stage: str, fields: Dict[str, Any]) -> None:
        path = _dump_path()
        header = (
            "\n===== PAW scan watchdog: no progress for "
            f"{fields.get('idle_seconds')}s (stage={stage}, file={fields.get('file')}, "
            f"hash={fields.get('hash')}) =====\n"
        )
        try:
            if path:
                with open(path, "a", encoding="utf-8", errors="replace") as stream:
                    stream.write(header)
                    stream.flush()
                    faulthandler.dump_traceback(file=stream, all_threads=True)
                    stream.write("===== end of thread dump =====\n")
                    stream.flush()
                return
        except Exception:
            pass
        try:
            faulthandler.dump_traceback(file=None, all_threads=True)
        except Exception:
            pass
