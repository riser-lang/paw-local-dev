"""Canonical production committer hook installer for governed writes.

Installs Source Version Governance and Project Soul executors deterministically
at process startup.  Callers should use ``commit_approved_change`` from this
module (or ensure integrations are installed before binding committer aliases).

v1.3B Patch 1: after a successful Formal State commit, a bounded retrieval
refresh runs in-process for knowledge targets. Formal State remains canonical:
a refresh failure never rolls back the commit; stale state is detectable via
watermark mismatch (RETRIEVAL_STALE) at query time.
"""

from __future__ import annotations

from pathlib import Path
import os

from typing import Any
_INSTALLED = False

def install_production_committer_integrations() -> None:
    """Install all governed committer hooks exactly once per process."""
    global _INSTALLED
    if _INSTALLED:
        return
    from paw_source_version_governance import install_committer_integration as _install_governance
    from paw_project_soul_bootstrap import install_committer_integration as _install_project_soul
    _install_governance()
    _install_project_soul()
    _INSTALLED = True

def is_project_create_executor_active() -> bool:
    install_production_committer_integrations()
    import committer
    return ("project", "create") in committer.EXECUTORS

def _post_commit_retrieval_refresh(result: dict[str, Any], db_path: str | None) -> dict[str, Any] | None:
    """v1.3B Patch 1: bounded retrieval refresh AFTER Formal commit success.
    Best-effort, in-process, no new infrastructure. Never raises into the
    committer path: a refresh failure is reported, not propagated."""
    if result.get("result") != "success":
        return None
    try:
        from paw_retrieval_refresh_hook import refresh_after_commit
        return refresh_after_commit(result, db_path=db_path or _default_db_path())
    except Exception:  # noqa: BLE001
        # Formal commit stays committed; stale detectable via watermark.
        return None

def _default_db_path() -> str:
    import committer as _committer
    return str(getattr(_committer, "DB_PATH", os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))))

def commit_approved_change(change_id: str, *, db_path: str | None = None) -> dict[str, Any]:
    """Commit through the canonical committer with all governed hooks installed.
    On success, runs the bounded retrieval refresh hook (Patch 1) and attaches
    its report as result['retrieval_refresh'] when available."""
    install_production_committer_integrations()
    import committer as committer_mod
    previous_db_path = getattr(committer_mod, "DB_PATH", None)
    if db_path is not None:
        committer_mod.DB_PATH = db_path
    try:
        result = committer_mod.commit_approved_change(change_id)
        if result.get("result") == "success":
            refresh_report = _post_commit_retrieval_refresh(
                result, db_path or previous_db_path or _default_db_path())
            if refresh_report is not None:
                result = dict(result)
                result["retrieval_refresh"] = refresh_report
        return result
    finally:
        if db_path is not None:
            committer_mod.DB_PATH = previous_db_path if previous_db_path is not None else db_path

__all__ = [
    "commit_approved_change",
    "install_production_committer_integrations",
    "is_project_create_executor_active",
]
