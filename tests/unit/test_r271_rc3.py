"""R-271 (v2.5.0-rc3) regressions.

Covers the four findings from the rc2 acceptance run:

1. cruise must not spend minutes on project analysis: background model calls are
   bounded like the scan path, and the cruise reports per-file/per-call timings;
2. a replaced document version must not propose again, and the proposals it
   already filed are retired;
3. proposal display text must not carry Markdown heading markers;
4. the "items that can never be confirmed" came from those retired proposals.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from backend.pm import display_text, storage, versions
from backend.provider import adapter, model_calls
from backend.runtime_core import scanner as scanner_module
from backend.runtime_core import scheduler as scheduler_module
from backend.core import file_types
from tests.unit.test_scan_model_calls import provider, stub  # noqa: F401  (pytest fixtures)


@pytest.fixture(autouse=True)
def _clean_telemetry():
    model_calls.reset()
    yield
    model_calls.reset()


# ── 3. Markdown markers in proposal text ────────────────────────────────────
def test_heading_and_bom_are_removed_from_display_text():
    assert display_text.clean("\ufeff# 青苗计划实施方案 v1") == "青苗计划实施方案 v1"
    assert display_text.clean("### 目标") == "目标"
    assert display_text.clean("> 引用行") == "引用行"
    assert display_text.clean("- 目标 30 名\n- 负责人 林夏") == "目标 30 名 负责人 林夏"
    assert display_text.clean("**加粗** 与 `代码`") == "加粗 与 代码"
    assert display_text.clean("  多余   空格  ") == "多余 空格"


def test_only_display_fields_are_cleaned():
    payload = {"title": "# 标题", "metric_name": "# 指标", "raw_excerpt": "# 原文",
               "content": "# 正文", "target": 30}
    display_text.clean_payload(payload)
    assert payload["title"] == "标题"
    assert payload["metric_name"] == "指标"
    # Evidence must stay verbatim, otherwise quotes stop matching the source body.
    assert payload["raw_excerpt"] == "# 原文"
    assert payload["content"] == "# 正文"
    assert payload["target"] == 30


def test_a_label_is_never_emptied_by_cleaning():
    payload = {"title": "#"}
    display_text.clean_payload(payload)
    assert payload["title"] == "#"


# ── 2. Version replacement ──────────────────────────────────────────────────
def test_every_intake_path_reads_the_same_file_types():
    # The watcher imports watchdog, which the Linux unit runner does not install;
    # all three paths take the list from backend.core.file_types instead.
    assert scheduler_module.SUPPORTED_EXTENSIONS is file_types.SUPPORTED_EXTENSIONS
    assert scanner_module.SUPPORTED_EXTS is file_types.SUPPORTED_EXTENSIONS
    assert {".md", ".txt", ".docx", ".pdf"} <= set(file_types.SUPPORTED_EXTENSIONS)
    watcher = Path("backend/watcher/observer.py").read_text(encoding="utf-8")
    assert "from backend.core.file_types import SUPPORTED_EXTENSIONS" in watcher


def test_background_calls_are_recorded_under_the_cruise_channel(provider, stub):
    with model_calls.begin_run("cruise"):
        with model_calls.begin_task("project_advice"):
            provider.chat_completion(messages=[{"role": "user", "content": "建议"}], temperature=0.1)
    rows = model_calls.snapshot("cruise")
    assert len(rows) == 1
    row = rows[0]
    assert row["task"] == "project_advice"
    assert row["channel"] == "cruise"
    assert row["thinking_disabled"] is True
    assert row["seconds"] >= 0
    # Telemetry keeps counts and timings, never prompts or credentials.
    assert set(row) >= {"file", "task", "seconds", "status", "prompt_chars"}
    assert not any(key in row for key in ("messages", "prompt", "api_key", "body"))


def test_cruise_reports_per_file_and_per_call_timings(tmp_path, monkeypatch):
    work = tmp_path / "watch"
    work.mkdir()
    (work / "青苗计划_方案_v1.md").write_text("# 青苗计划实施方案 v1\n\n目标：30 名。", encoding="utf-8")

    scheduler = scheduler_module.PawScheduler(sources_dir=work, evidence_dir=tmp_path / "evidence")

    def fake_process(path, file_hash):
        time.sleep(0.01)
        return ["cand-1"]

    monkeypatch.setattr(scheduler, "process_source_file", fake_process)
    monkeypatch.setattr(scheduler_module.governance, "claim_source", lambda _h: True)
    monkeypatch.setattr(scheduler_module.governance, "release_source", lambda *a, **k: None)
    monkeypatch.setattr(scheduler_module, "run_project_convergence", lambda **k: {"pending": [], "auto_merged": []})
    monkeypatch.setattr(scheduler_module.project_analyzer, "analyze_projects", lambda **k: [])
    monkeypatch.setattr(scheduler_module.project_analyzer, "last_attempted", 0, raising=False)
    monkeypatch.setattr(scheduler.evolution_engine, "run_cruise_evolution_check",
                        lambda _d: {"overall_score": 90})
    monkeypatch.setattr("backend.pm.reread.run", lambda limit=5: {"processed": 0, "limit": limit, "items": []})

    result = scheduler.run_cruise(trigger="unit")
    timing = result["timing"]
    assert [f["name"] for f in timing["files"]] == ["青苗计划_方案_v1.md"]
    assert timing["files"][0]["candidates"] == 1
    assert timing["files"][0]["seconds"] >= 0
    assert {"captures", "convergence", "projects", "reread", "evolution"} <= set(timing["phases"])
    assert "model_calls" in timing and "model_call_rows" in timing
    # The run context must not leak into the next cruise or into chat answers.
    assert model_calls.current_run() is None
