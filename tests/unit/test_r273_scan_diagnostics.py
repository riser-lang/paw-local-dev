"""A stuck Phase 2 must explain itself and must not wait forever.

The acceptance runs showed a scan sitting in ``phase2_analyzing`` past the
harness window while the HTTP side of the same process answered normally.  These
tests pin the three properties that make such a stall explainable and bounded:

* the shared claim registry names the pipeline that owns a file version, and a
  waiter logs who it is waiting for;
* the scan stops waiting for another pipeline after a bounded time instead of
  hanging (and instead of failing the whole scan);
* one unavailable model call cannot hold a file version for a minute, because
  retries stop once the budget is spent;
* when a file makes no progress, every thread stack is written to the log.
"""
from __future__ import annotations

import logging
import time

import pytest

from backend.governance import service as governance_service
from backend.runtime_core import scanner as scanner_module
from backend.runtime_core import scan_diagnostics
from backend.governance.service import GovernanceService

gov = GovernanceService()


def _clear_registry():
    with governance_service._source_lock:
        governance_service._sources_in_progress.clear()


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


def test_claim_registry_names_the_owner_and_the_wait(monkeypatch):
    # The registry is what matters here; the database check belongs to the
    # workspace tests and would make this depend on whatever they left behind.
    monkeypatch.setattr(gov, "_is_source_recorded", lambda _hash: False)
    assert gov.claim_source("hash-owner", "watcher") is True
    registry = gov.claim_registry()
    assert registry["hash-owner"]["pipeline"] == "watcher"
    assert registry["hash-owner"]["thread"]
    # A second pipeline cannot take the version while it is owned.
    assert gov.claim_source("hash-owner", "scan") is False
    waited = time.monotonic()
    gov.release_source("hash-owner", "doc.docx", "watcher", candidates_created=2)
    assert gov.claim_registry() == {}
    assert time.monotonic() - waited < 2.0


def test_wait_reports_the_owner_it_is_waiting_for(caplog):
    gov.claim_source("hash-wait", "cruise")
    with caplog.at_level(logging.INFO, logger="paw.scan.diag"):
        with pytest.raises(TimeoutError):
            gov.wait_for_source_completion("hash-wait", timeout=0.2)
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "claim_wait_timeout" in text
    assert "owner=cruise" in text


def test_scan_does_not_wait_for_another_pipeline_longer_than_its_budget():
    # The scanner's own budget is the number that keeps a scan inside the
    # acceptance window even when another pipeline owns the file.
    assert scanner_module.SCAN_OWNER_WAIT_SECONDS <= 20.0
    assert scanner_module.SCAN_OWNER_WAIT_SECONDS >= 1.0


def test_retry_budget_stops_a_retry_storm_before_a_minute():
    from backend.provider import adapter

    assert adapter.SCAN_MAX_RETRIES == 2
    # Three attempts at the per-call timeout would be 60 s; the budget stops the
    # loop well before that, so one unavailable call cannot hold a claim.
    three_attempts = 3 * adapter.SCAN_CALL_TIMEOUT_SECONDS
    assert adapter.SCAN_RETRY_BUDGET_SECONDS < three_attempts
    assert adapter.SCAN_RETRY_BUDGET_SECONDS <= 30.0


def test_watchdog_dumps_every_thread_when_a_file_stops_progressing(tmp_path, monkeypatch):
    log_path = tmp_path / "paw-desktop.log"
    log_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(scan_diagnostics, "_dump_path", lambda: str(log_path))

    class StalledScanner:
        status = "phase2_analyzing"
        current_file = "研制方案_v1.docx"

        def phase2_diagnostics(self):
            return {
                "active": True,
                "file": "研制方案_v1.docx",
                "hash": "abcdef1234567890",
                "stage": "waiting_owner",
                "progress_at": time.monotonic() - 30,
                "file_started_at": time.monotonic() - 30,
                "file_seconds": 30.0,
            }

    watchdog = scan_diagnostics.Phase2Watchdog(StalledScanner(), stall_seconds=10)
    assert watchdog.check_once() is True

    text = log_path.read_text(encoding="utf-8", errors="replace")
    assert "no progress" in text
    # A real thread dump: at least one stack frame and the closing marker.
    assert 'File "' in text
    assert "end of thread dump" in text
    assert "waiting_owner" in text
    # Nothing about prompts, request bodies or credentials.
    assert "sk-" not in text


def test_watchdog_ignores_a_file_that_is_still_moving():
    class MovingScanner:
        def phase2_diagnostics(self):
            return {"active": True, "file": "a.docx", "hash": "h",
                    "stage": "model", "progress_at": time.monotonic(),
                    "file_seconds": 0.4}

    watchdog = scan_diagnostics.Phase2Watchdog(MovingScanner(), stall_seconds=10)
    assert watchdog.check_once() is False


def test_log_file_redacts_a_key(tmp_path, monkeypatch):
    from backend.core import log_setup

    monkeypatch.setattr(log_setup, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(log_setup, "mirror_dir", lambda: tmp_path / "mirror")
    monkeypatch.setitem(log_setup.__dict__, "_configured", False)
    path = log_setup.ensure_file_logging()

    logging.getLogger("paw.scan.diag").info("model call with key sk-abcdef1234567890abcdef")
    for handler in logging.getLogger("paw.scan.diag").handlers:
        handler.flush()
    text = path.read_text(encoding="utf-8", errors="replace")
    assert "sk-abcdef1234567890abcdef" not in text
    assert "REDACTED" in text
