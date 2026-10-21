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

from backend.pm import cockpit, display_text, storage, versions
from backend.provider import adapter, model_calls
from backend.runtime_core import planner as planner_module
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
def test_version_family_ignores_the_shared_project_prefix():
    # Two different documents of one project must not look like one family, and
    # therefore must not fall back to "needs assignment" (which is what stopped
    # the v1 -> v2 replacement from happening).
    assert versions.similar_family("青苗计划_方案", "青苗计划_10月周报", "青苗计划") is False
    assert versions.similar_family("青苗计划_方案", "青苗计划_方案", "青苗计划") is True
    assert versions.similar_family("青苗计划_方案", "青苗计划_方案修订", "青苗计划") is True


@pytest.fixture
def version_db(tmp_path, monkeypatch):
    db = tmp_path / "rc3.db"
    monkeypatch.setenv("PAW_WORKSPACE_DB", str(db))
    real_connect = storage.connect
    monkeypatch.setattr(storage, "connect", lambda db_path=None: real_connect(db))
    with storage.connect() as conn:
        conn.executescript('''
          CREATE TABLE project(id TEXT PRIMARY KEY,name TEXT,status TEXT,description TEXT,
            latest_progress_summary TEXT,next_action_summary TEXT,updated_at TEXT);
          CREATE TABLE review_decision(id TEXT PRIMARY KEY,candidate_id TEXT,decision TEXT,reviewer TEXT,
            review_channel TEXT,approval_source TEXT,original_payload TEXT,reviewed_payload TEXT,
            authorization_payload_hash TEXT,review_notes TEXT,created_at TEXT);
          CREATE TABLE approved_change(change_id TEXT PRIMARY KEY,target_type TEXT,operation TEXT,payload TEXT,
            approval_ref TEXT,source_refs TEXT,expected_target_version TEXT,idempotency_key TEXT,created_at TEXT,status TEXT);
          CREATE TABLE commit_record(id TEXT PRIMARY KEY,change_id TEXT,transaction_id TEXT,result TEXT,committed_at TEXT,error TEXT);
          CREATE TABLE audit_event(id TEXT PRIMARY KEY,event_type TEXT,actor TEXT,target_type TEXT,target_id TEXT,
            change_id TEXT,commit_id TEXT,details TEXT,created_at TEXT);
          CREATE TABLE candidate(id TEXT PRIMARY KEY,candidate_type TEXT,source_refs TEXT,raw_excerpt TEXT,
            proposed_change TEXT,confidence TEXT,analysis_model TEXT,status TEXT,created_at TEXT,
            reviewed_at TEXT,reviewed_by TEXT);
        ''')
        conn.execute("INSERT INTO project VALUES('p','青苗计划','active','','','','initial')")
        storage.migrate(conn)
    return db


def _ingest(sid, title, body):
    with storage.connect() as conn:
        storage.store_source(conn, sid, title, body)
    versions.process_source(sid)


def _candidate(sid, metric, target):
    payload = {"target_type": "data", "operation": "create",
               "payload": {"project_name": "青苗计划", "metric_name": metric, "target": target}}
    with storage.connect() as conn:
        conn.execute("INSERT INTO candidate(id,candidate_type,source_refs,raw_excerpt,proposed_change,"
                     "confidence,analysis_model,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (f"c-{sid[:6]}", "data", json.dumps([sid]), metric,
                      json.dumps(payload, ensure_ascii=False), "HIGH", "unit", "pending", "now"))
    return f"c-{sid[:6]}"


def test_older_version_is_reported_as_superseded(version_db):
    _ingest("v1", "青苗计划_方案_v1.md", "# 青苗计划实施方案 v1\n\n生效日期：2026年9月1日\n目标：30 名。")
    _ingest("v2", "青苗计划_方案_v2.md", "# 青苗计划实施方案 v2\n\n生效日期：2026年10月8日\n目标调整：40 名。")
    with storage.connect() as conn:
        assert versions.is_replaced(conn, "v1") is True
        assert versions.is_replaced(conn, "v2") is False
        # Both versions really did join one family and the ruling was recorded.
        assert conn.execute("SELECT count(*) FROM pm_family").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM pm_version_ruling").fetchone()[0] == 2


def test_replaced_is_decided_by_the_version_ruling(version_db):
    # The family pointer is authoritative: before the ruling neither version is
    # replaced (both may propose), after it only the current one may.
    _ingest("v1", "青苗计划_方案_v1.md", "# 青苗计划实施方案 v1\n\n生效日期：2026年9月1日\n目标：30 名。")
    with storage.connect() as conn:
        assert versions.is_replaced(conn, "v1") is False
    _ingest("v2", "青苗计划_方案_v2.md", "# 青苗计划实施方案 v2\n\n生效日期：2026年10月8日\n目标调整：40 名。")
    with storage.connect() as conn:
        assert versions.is_replaced(conn, "v1") is True
        assert versions.is_replaced(conn, "v2") is False
    # A source outside any family is never "replaced".
    with storage.connect() as conn:
        storage.store_source(conn, "solo", "青禾计划_纪要.md", "会议记录。")
    versions.process_source("solo")
    with storage.connect() as conn:
        assert versions.is_replaced(conn, "solo") is False


def test_retired_proposals_leave_the_review_list(version_db):
    """The candidates that could never be confirmed are retired with an audit note."""
    _ingest("v1", "青苗计划_方案_v1.md", "# 青苗计划实施方案 v1\n\n生效日期：2026年9月1日\n目标：30 名。")
    stale = _candidate("v1", "第一期培养新员工导师", 30)
    _ingest("v2", "青苗计划_方案_v2.md", "# 青苗计划实施方案 v2\n\n生效日期：2026年10月8日\n目标调整：40 名。")
    current = _candidate("v2", "第一期培养新员工导师", 40)

    with storage.connect() as conn:
        rows = {r["id"]: r for r in conn.execute("SELECT id,status,reviewed_by FROM candidate")}
        assert rows[stale]["status"] == "rejected"
        assert rows[stale]["reviewed_by"] == "system:version-superseded"
        assert rows[current]["status"] == "pending"
        note = conn.execute("SELECT review_notes FROM review_decision WHERE candidate_id=?", (stale,)).fetchone()[0]
        assert "来源版本已被替换" in note
        # Only the current version's proposal is left for the same field.
        pending = [dict(r) for r in conn.execute("SELECT proposed_change FROM candidate WHERE status='pending'")]
        assert len(pending) == 1
        assert json.loads(pending[0]["proposed_change"])["payload"]["target"] == 40


def test_current_version_proposals_survive_the_older_version_guard(version_db):
    """Only the replaced version's proposals are retired, never the current ones."""
    _ingest("v1", "青苗计划_方案_v1.md", "# 青苗计划实施方案 v1\n\n生效日期：2026年9月1日\n目标：30 名。")
    stale = _candidate("v1", "第一期培养新员工导师", 30)
    _ingest("v2", "青苗计划_方案_v2.md", "# 青苗计划实施方案 v2\n\n生效日期：2026年10月8日\n目标调整：40 名。")
    current = _candidate("v2", "第一期培养新员工导师", 40)
    # The guard a replaced version runs must not touch the current version's card.
    with storage.connect() as conn:
        versions.withdraw_pending_candidates(conn, ["v1"])
        rows = {r["id"]: r["status"] for r in conn.execute("SELECT id,status FROM candidate")}
    assert rows[stale] == "rejected"
    assert rows[current] == "pending"


def test_planner_does_not_propose_from_a_replaced_version(version_db, monkeypatch):
    _ingest("v1", "青苗计划_方案_v1.md", "# 青苗计划实施方案 v1\n\n生效日期：2026年9月1日\n目标：30 名。")
    _ingest("v2", "青苗计划_方案_v2.md", "# 青苗计划实施方案 v2\n\n生效日期：2026年10月8日\n目标调整：40 名。")

    called = []
    monkeypatch.setattr(planner_module, "get_active_provider", lambda: called.append("provider"))
    evidence = {"display_name": "青苗计划_方案_v1.md", "raw_artifact_sha256": "v1",
                "text_blocks": [{"index": 0, "text": "目标：30 名。"}]}
    assert planner_module.agent_planner.plan_document_impact(evidence, "v1.md", "v1") == []
    assert called == [], "a replaced version must not reach the model"


# ── 1. Cruise cost ──────────────────────────────────────────────────────────
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


def test_project_drafts_are_produced_inside_the_cruise(tmp_path, monkeypatch):
    """No background model caller: the project drafts run on the cruise thread.

    A second model caller inside one app process competed with the next scan and
    made the acceptance scans time out, so the deferral was removed.  This test
    fails if it comes back.
    """
    import threading

    work = tmp_path / "watch"
    work.mkdir()
    (work / "青苗计划_方案_v1.md").write_text("# 青苗计划实施方案 v1\n\n目标：30 名。", encoding="utf-8")
    scheduler = scheduler_module.PawScheduler(sources_dir=work, evidence_dir=tmp_path / "evidence")

    seen = {}

    def fake_analyze(max_projects=10, force=False):
        seen["thread"] = threading.current_thread().name
        seen["max_projects"] = max_projects
        return [{"name": "青苗计划"}]

    monkeypatch.setattr(scheduler, "process_source_file", lambda path, file_hash: ["cand-1"])
    monkeypatch.setattr(scheduler_module.governance, "claim_source", lambda _h: True)
    monkeypatch.setattr(scheduler_module.governance, "release_source", lambda *a, **k: None)
    monkeypatch.setattr(scheduler_module, "run_project_convergence", lambda **k: {"pending": [], "auto_merged": []})
    monkeypatch.setattr(scheduler_module.project_analyzer, "analyze_projects", fake_analyze)
    monkeypatch.setattr(scheduler_module.project_analyzer, "last_attempted", 1, raising=False)
    monkeypatch.setattr(scheduler.evolution_engine, "run_cruise_evolution_check", lambda _d: {"overall_score": 90})
    monkeypatch.setattr("backend.pm.reread.run", lambda limit=5: {"processed": 0, "limit": limit, "items": []})

    result = scheduler.run_cruise(trigger="unit")
    assert seen["thread"] == threading.current_thread().name, "project drafts must not move to a background thread"
    assert result["projects_analyzed"] == 1
    assert result["projects_details"] == ["青苗计划"]
    assert "projects_deferred" not in result
    assert not any(t.name == "CruiseProjectAnalysis" for t in threading.enumerate())


def test_startup_cleanup_retires_what_an_older_build_left_pending(version_db):
    """The "items that can never be confirmed" are cleaned up once at startup."""
    _ingest("v1", "青苗计划_方案_v1.md", "# 青苗计划实施方案 v1\n\n生效日期：2026年9月1日\n目标：30 名。")
    stale = _candidate("v1", "第一期培养新员工导师", 30)
    _ingest("v2", "青苗计划_方案_v2.md", "# 青苗计划实施方案 v2\n\n生效日期：2026年10月8日\n目标调整：40 名。")
    current = _candidate("v2", "第一期培养新员工导师", 40)
    # Simulate a workspace written before the ruling retired proposals: put the
    # old card back into the review list without a review decision.
    with storage.connect() as conn:
        conn.execute("UPDATE candidate SET status='pending',reviewed_at=NULL,reviewed_by=NULL WHERE id=?", (stale,))
        conn.execute("DELETE FROM review_decision WHERE candidate_id=?", (stale,))

    assert versions.retire_replaced_candidates() == 1
    with storage.connect() as conn:
        statuses = {r["id"]: r["status"] for r in conn.execute("SELECT id,status FROM candidate")}
    assert statuses[stale] == "rejected"
    assert statuses[current] == "pending"
    # Idempotent: a second startup finds nothing to do.
    assert versions.retire_replaced_candidates() == 0


def test_pending_target_still_names_the_value_it_replaces(version_db):
    """Retiring the old version's proposal must not hide "from 30 to 40"."""
    _ingest("v1", "青苗计划_方案_v1.md", "# 青苗计划实施方案 v1\n\n生效日期：2026年9月1日\n目标：30 名。")
    stale = _candidate("v1", "第一期培养新员工导师", 30)
    _ingest("v2", "青苗计划_方案_v2.md", "# 青苗计划实施方案 v2\n\n生效日期：2026年10月8日\n目标调整：40 名。")
    current = _candidate("v2", "第一期培养新员工导师", 40)

    with storage.connect() as conn:
        statuses = {r["id"]: r["status"] for r in conn.execute("SELECT id,status FROM candidate")}
        assert statuses[stale] == "rejected"
        view = cockpit.project_vm(conn, "p")
    targets = view["pending_targets"]
    assert len(targets) == 1
    assert targets[0]["id"] == current
    assert targets[0]["previous"] == 30
    assert targets[0]["target"] == 40
    assert targets[0]["pending_label"] == "待确认：目标由 30 改为 40"
    # The evidence the installed-app case prints says which file proposed "30".
    assert targets[0]["previous_source_title"] == "青苗计划_方案_v1.md"
    assert targets[0]["source_title"] == "青苗计划_方案_v2.md"
