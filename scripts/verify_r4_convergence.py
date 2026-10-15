"""PAW v2.3.3 regression suite: AI assistant global context, schema migration,
project convergence, decided-proposal de-duplication and one-analysis-per-file-version.

Runs against a temporary copy of data/workspace.db (old schema, so the
migration is exercised for real). No real model is called: the provider is
replaced by a counting fake. Writes its log to verification_logs/r4_convergence.log.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="paw_r4_"))
os.environ["PAW_DATA_DIR"] = str(TMP)
os.environ["PAW_WORKSPACE_DB"] = str(TMP / "workspace.db")
os.environ["PAW_CHAT_HISTORY_DB"] = str(TMP / "chat_history.db")
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

src = sqlite3.connect(f"file:{ROOT / 'data' / 'workspace.db'}?mode=ro", uri=True)
dst = sqlite3.connect(str(TMP / "workspace.db"))
src.backup(dst); dst.close(); src.close()
(TMP / "sources").mkdir()
(TMP / "config.json").write_text(json.dumps({"provider": "deepseek", "deepseek_api_key": "", "web_search_enabled": False,
                                             "watch_enabled": False, "work_dir": str(TMP / "sources")}), encoding="utf-8")

LOG = ROOT / "verification_logs" / "r4_convergence.log"
lines: list[str] = []


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    lines.append(line)


def db():
    c = sqlite3.connect(str(TMP / "workspace.db"))
    c.row_factory = sqlite3.Row
    return c


def table_sql(name: str) -> str:
    c = db()
    try:
        return c.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
    finally:
        c.close()


def add_project(name: str, created_at: str) -> str:
    import uuid
    pid = str(uuid.uuid4())
    c = db()
    with c:
        c.execute("INSERT OR IGNORE INTO domain (id, slug, name, description, is_enabled, sort_order, created_at, updated_at) "
                  "VALUES ('domain-default','default','默认业务域','',1,0,?,?)", (created_at, created_at))
        c.execute("INSERT INTO project (id, domain_id, name, description, status, stage, priority, privacy_level, created_at, updated_at) "
                  "VALUES (?, 'domain-default', ?, '', 'active', 'executing', 'medium', 'normal', ?, ?)",
                  (pid, name, created_at, created_at))
    c.close()
    return pid


# ── tests ────────────────────────────────────────────────────────────────────

def t1_schema_migration():
    from backend.core.schema_migration import migrate_check_constraints, pending_changes
    c = db(); before = pending_changes(c); counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("candidate", "approved_change")}; c.close()
    assert set(before) == {"candidate", "approved_change"}, f"expected old schema in test copy, pending={list(before)}"
    res = migrate_check_constraints(TMP / "workspace.db")
    assert res["integrity"] == "ok", res
    assert "'project_merge'" in table_sql("candidate")
    assert "'project_merge'" in table_sql("approved_change") and "'merge'" in table_sql("approved_change")
    c = db(); after = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in counts}; c.close()
    assert after == counts, f"row counts changed {counts} -> {after}"
    assert Path(res["backup"]).is_file(), "backup missing"
    assert migrate_check_constraints(TMP / "workspace.db")["migrated"] == [], "second run must be a no-op"
    return f"migrated {res['migrated']}, rows unchanged {after}, integrity ok, backup written, second run no-op"


def t2_assistant_global_question():
    c = db()
    with c:
        c.execute("INSERT INTO todo (id, title, status, priority, created_at, updated_at) "
                  "VALUES ('r4-todo-1', 'R4 全局待办', 'pending', 'high', '2026-10-07T00:00:00Z', '2026-10-07T00:00:00Z')")
    c.close()
    from backend.core.config import DB_PATH
    from backend.brain.runtime.agy_v2_runtime import build_v2_turn
    from backend.brain.api.local_agy_facade import create_chat_session, send_chat_message, WebAgyCreateRequest, WebAgyMessageRequest
    build_v2_turn("对项目进行收敛", "chat", db_path=str(DB_PATH))   # exact input that raised ValueError in v2.3.2
    turn = build_v2_turn("今天有哪些待办？", "chat", db_path=str(DB_PATH))
    sess = create_chat_session(WebAgyCreateRequest(mode="chat"))
    c = db(); n_cand = c.execute("SELECT COUNT(*) FROM candidate").fetchone()[0]; n_proj = c.execute("SELECT COUNT(*) FROM project").fetchone()[0]; c.close()
    res = send_chat_message(sess["session_id"], WebAgyMessageRequest(message="今天有哪些待办？"))
    assert res.get("answer_type") == "todo_overview" and "R4 全局待办" in res.get("response", ""), res.get("response", "")[:200]
    conv = send_chat_message(sess["session_id"], WebAgyMessageRequest(message="对项目进行收敛"))
    assert conv.get("answer_type") == "project_convergence_report", conv.get("answer_type")
    # v2.3.4: unrelated questions go to general chat (no workspace data); without an
    # API key the assistant says it needs a model instead of answering from the workspace.
    other = send_chat_message(sess["session_id"], WebAgyMessageRequest(message="明天中午吃什么比较好？"))
    assert other.get("answer_type") == "general_chat" and other.get("status") == "OFFLINE_GENERAL_CHAT", \
        (other.get("answer_type"), other.get("status"))
    c = db(); n_cand2 = c.execute("SELECT COUNT(*) FROM candidate").fetchone()[0]; n_proj2 = c.execute("SELECT COUNT(*) FROM project").fetchone()[0]; c.close()
    assert (n_cand2, n_proj2) == (n_cand, n_proj), "workspace reports must be read-only"
    return (f"global context builds without error (fail_closed={turn.get('fail_closed')}); '今天有哪些待办？' lists open todos; "
            f"'对项目进行收敛' gets a duplicate report; unrelated question routed to general chat; no rows written")


def t3_convergence_and_merge():
    from backend.runtime_core.project_convergence import run_project_convergence, normalize_project_name
    from backend.governance.service import governance
    keep = add_project("R4测试·人才盘点（第1期）", "2026-01-01T00:00:00Z")
    dup1 = add_project("R4测试人才盘点（第1期）项目", "2026-02-01T00:00:00Z")
    dup2 = add_project("r4测试 人才盘点 第1期", "2026-03-01T00:00:00Z")
    other = add_project("R4测试人才盘点（第2期）", "2026-01-15T00:00:00Z")
    assert normalize_project_name("R4测试人才盘点（第1期）") != normalize_project_name("R4测试人才盘点（第2期）")
    c = db()
    with c:
        c.execute("INSERT INTO todo (id, title, status, priority, project_id, created_at, updated_at) "
                  "VALUES ('r4-todo-dup', '挂在重复项目上的待办', 'pending', 'medium', ?, '2026-10-07T00:00:00Z', '2026-10-07T00:00:00Z')", (dup1,))
    c.close()

    # v2.3.8: same-name duplicates are high confidence and merge automatically
    # through governance (reviewer auto:project_convergence, backup first).
    res = run_project_convergence()
    mine = [m for m in res["auto_merged"] if m["keeper"] == "R4测试·人才盘点（第1期）"]
    assert len(mine) == 1, f"expected the R4 group to be auto-merged, got {res['auto_merged']}"
    assert set(mine[0]["merged"]) == {"R4测试人才盘点（第1期）项目", "r4测试 人才盘点 第1期"}, mine[0]
    c = db()
    st = {r["id"]: r["status"] for r in c.execute("SELECT id, status FROM project WHERE id IN (?,?,?,?)", (keep, dup1, dup2, other))}
    todo_pid = c.execute("SELECT project_id FROM todo WHERE id='r4-todo-dup'").fetchone()[0]
    reviewer = c.execute("SELECT reviewer FROM review_decision WHERE candidate_id=?", (mine[0]["candidate_id"],)).fetchone()[0]
    c.close()
    assert st[dup1] == "merged" and st[dup2] == "merged" and st[keep] == "active" and st[other] == "active", st
    assert todo_pid == keep, "todo was not moved to the kept project"
    assert reviewer == "auto:project_convergence", reviewer

    again = run_project_convergence()
    assert not again["candidates"] and not again["auto_merged"], "re-running convergence must not file or merge again"
    return (f"{res['groups']} duplicate groups found; R4 group auto-merged (2 duplicates into keeper, reviewer recorded), "
            f"other period untouched, todo moved; re-run filed nothing")


def t4_decided_not_refiled():
    from backend.governance.service import governance
    kw = dict(target_type="todo", operation="create", payload={"title": "R4 已驳回过的待办", "priority": "medium"},
              raw_excerpt="R4", source_refs=["r4-src"], confidence="HIGH", analysis_model="r4-test")
    cid = governance.create_candidate(**json.loads(json.dumps(kw)))
    governance.reject_candidate(cid, reviewer="r4-test", notes="duplicate check")
    again = governance.create_candidate(**json.loads(json.dumps(kw)))
    c = db(); n = c.execute("SELECT COUNT(*) FROM candidate WHERE raw_excerpt='R4' AND status='pending'").fetchone()[0]; c.close()
    assert again == cid and n == 0, f"rejected proposal came back (id={again}, pending={n})"
    return "a proposal the user rejected is not put back into the review queue"


def t5_project_create_reuses_existing():
    from backend.governance.service import governance
    pid = add_project("R4唯一项目名", "2026-04-01T00:00:00Z")
    cid = governance.create_candidate(target_type="project", operation="create", payload={"name": "R4唯一项目名（项目）"},
                                      raw_excerpt="R4-create", confidence="HIGH", analysis_model="r4-test")
    change = json.loads(db().execute("SELECT proposed_change FROM candidate WHERE id=?", (cid,)).fetchone()[0])
    assert change["operation"] == "update" and change["payload"]["id"] == pid, change
    c = db(); before = c.execute("SELECT COUNT(*) FROM project").fetchone()[0]; c.close()
    governance.accept_and_commit_candidate(cid, reviewer="r4-test")
    c = db(); after = c.execute("SELECT COUNT(*) FROM project").fetchone()[0]; c.close()
    assert after == before, f"duplicate project created ({before} -> {after})"
    return "a 'new project' proposal with an existing name becomes an update; no duplicate project is created"


def t6_one_analysis_per_file_version():
    import docx
    import backend.runtime_core.planner as planner
    from backend.runtime_core.scheduler import scheduler
    from backend.governance.service import governance

    calls = {"n": 0}

    class CountingProvider:
        def propose(self, evidence, projects, knowledge):
            calls["n"] += 1
            return [{"target_type": "knowledge", "operation": "create",
                     "payload": {"title": "R4 巡航知识", "content": "R4", "knowledge_type": "project", "status": "draft"},
                     "raw_excerpt": "R4 cruise", "confidence": "HIGH"}]

    src_dir = TMP / "cruise_src"; src_dir.mkdir()
    d = docx.Document(); d.add_paragraph("R4 巡航测试文档：同一版本只应分析一次。"); d.save(str(src_dir / "r4.docx"))
    orig = planner.get_active_provider
    old_dirs, old_mode = scheduler.sources_dirs, scheduler.mode
    planner.get_active_provider = lambda: CountingProvider()
    try:
        scheduler.sources_dirs = [src_dir]; scheduler.mode = "manual"
        r1 = scheduler.run_cruise(trigger="r4", force_rescan=False, analyze_projects=False)
        scheduler._processed_hashes.clear()          # simulate an app restart
        r2 = scheduler.run_cruise(trigger="r4", force_rescan=True, analyze_projects=False)   # deep cruise
    finally:
        planner.get_active_provider = orig
        scheduler.sources_dirs, scheduler.mode = old_dirs, old_mode
    assert calls["n"] == 1, f"model was called {calls['n']} times for one file version"
    assert r1["files_analyzed"] == 1 and r2["files_analyzed"] == 0, (r1["files_analyzed"], r2["files_analyzed"])
    from backend.reader.contract import artifact_sha256
    h = artifact_sha256(src_dir / "r4.docx")
    assert governance.is_source_analyzed(h)
    assert governance.claim_source("r4-claim") and not governance.claim_source("r4-claim"), "concurrent claim not blocked"
    governance.release_source("r4-claim")
    # A phase-1 version-review question carries the file hash but is not an AI analysis.
    governance.create_candidate(target_type="knowledge", operation="create",
                                payload={"title": "R4 版本认定问题", "slug": "r4-ver"}, raw_excerpt="R4 ver",
                                source_refs=["r4-phase1-only-hash"], confidence="MEDIUM",
                                analysis_model="Scanner+TemporalAuthorityResolver")
    assert not governance.is_source_analyzed("r4-phase1-only-hash"), "version-review question blocked AI analysis"
    return "one file version -> 1 model call across a normal cruise, a restart and a deep cruise; concurrent claims blocked"


def main() -> int:
    tests = [("R4-1", "旧库约束迁移", t1_schema_migration),
             ("R4-2", "AI 助手全局问题不再报错", t2_assistant_global_question),
             ("R4-3", "项目收敛与合并", t3_convergence_and_merge),
             ("R4-4", "已决定的提议不再回到待审核", t4_decided_not_refiled),
             ("R4-5", "同名项目不再重复创建", t5_project_create_reuses_existing),
             ("R4-6", "同一文件版本只分析一次", t6_one_analysis_per_file_version)]
    log("=" * 60)
    log("PAW v2.3.3 R4 回归测试（隔离临时库，假模型，不调用真实 API）")
    log("=" * 60)
    passed = 0
    for tid, title, fn in tests:
        t0 = time.time()
        try:
            detail = fn()
            passed += 1
            log(f"[PASS] {tid} {title} ({time.time() - t0:.2f}s): {detail}")
        except Exception as exc:
            log(f"[FAIL] {tid} {title} ({time.time() - t0:.2f}s): {exc}")
            log(traceback.format_exc())
    log(f"R4 汇总: 共 {len(tests)} 项，通过: {passed}，失败: {len(tests) - passed}")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    shutil.rmtree(TMP, ignore_errors=True)
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
