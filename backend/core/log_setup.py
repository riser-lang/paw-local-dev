"""File logging for the installed application.

The desktop build runs without a console (``console=False`` in the PyInstaller
spec), so records written to stdout are lost.  Acceptance failures need the
application's own account of what it was doing, so the same records are written
to one rotating file under %LOCALAPPDATA%\\PAW\\logs with the existing
secret-redaction filter installed.

A second copy is mirrored under %TEMP%\\paw-scan-diagnostics because the
acceptance cleanup deletes %LOCALAPPDATA%\\PAW between cases: a failure in one
case has to stay readable after the next case has already wiped the data
directory.  Only the PAW loggers are attached; provider and HTTP chatter stays
on stdout.
"""
from __future__ import annotations

import logging
import os
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

from backend.core.config import LOGS_DIR
from backend.core.security import SensitiveDataLogFilter

LOG_FILE_NAME = "paw-desktop.log"
MIRROR_DIR_NAME = "paw-scan-diagnostics"
MAX_BYTES = 8 * 1024 * 1024
BACKUP_COUNT = 3
MIRROR_MAX_BYTES = 4 * 1024 * 1024
MIRROR_BACKUP_COUNT = 2

# Diagnostics are emitted by these loggers; anything else stays on stdout only.
ATTACHED_LOGGERS = (
    "paw.scanner",
    "paw.scan.diag",
    "paw.governance",
    "paw.planner",
    "paw.scheduler",
    "paw.watcher",
    "paw.provider",
)

_configured = False


def log_file_path() -> Path:
    return LOGS_DIR / LOG_FILE_NAME


def mirror_dir() -> Path:
    """Copy of the log that survives the acceptance cleanup of %LOCALAPPDATA%\\PAW.

    Not ``tempfile.gettempdir()``: a frozen build points that at its own
    extraction directory, which is removed when the process exits, so the copy
    would disappear exactly when it is needed.  ``%LOCALAPPDATA%\\Temp`` is the
    stable per-user temp directory and is not touched by the acceptance cleanup.
    """
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) / "Temp" if local else Path(tempfile.gettempdir())
    return base / MIRROR_DIR_NAME


def _handler(path: Path, max_bytes: int, backup_count: int) -> RotatingFileHandler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    ))
    handler.addFilter(SensitiveDataLogFilter())
    return handler


def ensure_file_logging() -> Path:
    """Attach the rotating file handlers once and return the log path.

    Never raises: a read-only or missing data directory must not stop the app.
    """
    global _configured
    path = log_file_path()
    if _configured:
        return path
    try:
        handlers = [_handler(path, MAX_BYTES, BACKUP_COUNT)]
        for handler in handlers:
            for name in ATTACHED_LOGGERS:
                logger = logging.getLogger(name)
                logger.setLevel(logging.INFO)
                if not any(getattr(existing, "baseFilename", None)
                           == str(handler.baseFilename) for existing in logger.handlers):
                    logger.addHandler(handler)
        _configured = True
        # The mirrored copy survives the acceptance cleanup of the data
        # directory.  If it cannot be opened (locked, read-only TEMP), say so in
        # the primary log instead of failing silently: an empty artifact would
        # look like "the app had nothing to report".
        try:
            mirror_handler = _handler(mirror_dir() / LOG_FILE_NAME,
                                      MIRROR_MAX_BYTES, MIRROR_BACKUP_COUNT)
            for name in ATTACHED_LOGGERS:
                logging.getLogger(name).addHandler(mirror_handler)
        except Exception as exc:
            logging.getLogger("paw.scan.diag").warning(
                "scan diagnostics mirror unavailable: %s", type(exc).__name__)
    except Exception:
        pass
    return path
