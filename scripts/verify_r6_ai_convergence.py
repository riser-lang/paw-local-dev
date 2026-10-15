"""PAW v2.3.8 regression suite: AI project convergence during cruise.

Temporary copy of data/workspace.db, fake model; no real API is called.
Writes verification_logs/r6_ai_convergence.log.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="paw_r6_"))
os.environ["PAW_DATA_DIR"] = str(TMP)
os.environ["PAW_WORKSPACE_DB"] = str(TMP / "workspace.db")
os.environ["PAW_CHAT_HISTORY_DB"] = str(TMP / "chat_history.db")
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
s = sqlite3.connect(f"file:{ROOT / 'data' / 'workspace.db'}?mode=ro", uri=True)
d = sqlite3.connect(str(TMP / "workspace.db")); s.backup(d); d.close(); s.close()
(TMP / "config.json").write_text(json.dumps({"provider": "deepseek", "deepseek_api_key": "", "web_search_enabled": False,
                                             "watch_enabled": False}), encoding="utf-8")

LOG = ROOT / "verification_logs" / "r6_ai_convergence.log"
lines: list[str] = []


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line); lines.append(line)


from backend.core.config import settings  # noqa: E402
from backend.core.schema_migration import migrate_check_constraints  # noqa: E402
migrate_check_constraints(TMP / "workspace.db", backup=False)
import backend.provider.adapter as adapter  # noqa: E402
import backend.runtime_core.project_convergence as PC  # noqa: E402


def db():
    c = sqlite3.connect(str(TMP / "workspace.db")); c.row_factory = sqlite3.Row; return c


def add_project(name, created):
    pid = str(uuid.uuid4()); c = db()
    with c:
        c.execute("INSERT OR IGNORE INTO domain (id, slug, name, description, is_enabled, sort_order, created_at, updated_at) "
                  "VALUES ('domain-default','default','默认业务域','',1,0,?,?)", (created, created))
        c.execute("INSERT INTO project (id, domain_id, name, description, status, stage, priority, privacy_level, created_at, updated_at) "
                  "VALUES (?, 'domain-default', ?, '', 'active', 'executing', 'medium', 'normal', ?, ?)", (pid, name, created, created))
    c.close(); return pid


def status_of(pid):
    c = db(); r = c.execute("SELECT status FROM project WHERE id=?", (pid,)).fetchone(); c.close(); return r[0]


class FakeModel:
    model = "fake-convergence"

    def __init__(self):
        self.calls = 0
        self.plan = []          # list of (keeper_name, [member_names], confidence)

    def chat_completion(self, messages, temperature=0.2, max_tokens=2048, enable_web_search=None):
        self.calls += 1
        listing = messages[-1]["content"]
        idx = {m.group(2): int(m.group(1)) for m in re.finditer(r"^(\d+)\. (.+?)｜", listing, re.M)}
        groups = []
        for keeper, members, conf in self.plan:
            if keeper in idx and all(m in idx for m in members):
                groups.append({"keeper": idx[keeper], "members": [idx[m] for m in members], "confidence": conf,
                               "reason": "同一长期业务的不同部门/阶段"})
        groups.append({"keeper": 999, "members": [1], "confidence": "HIGH", "reason": "invalid index"})
        return ("```json\n" + json.dumps({"groups": groups}, ensure_ascii=False) + "\n```", {}, None)


fake = FakeModel()
adapter.get_active_provider = lambda: fake
settings.deepseek_api_key = "sk-r6-fake"
settings.daily_call_limit = 0

# fixtures
UMB = add_project("R6人才盘点", "2026-01-01T00:00:00Z")
F1 = add_project("R6人才盘点-财务部预评估", "2026-10-04T00:00:00Z")
F2 = add_project("R6人才盘点-市场部预评估", "2026-10-04T00:00:01Z")
S1 = add_project("R6销售大赛", "2026-02-01T00:00:00Z")
S2 = add_project("R6销售大赛复盘会", "2026-10-04T00:00:02Z")
_c = db()
EST = [r[0] for r in _c.execute("SELECT p.name FROM project p JOIN knowledge_project_link l ON l.project_id=p.id "
                                "GROUP BY p.id HAVING COUNT(*) >= 5 ORDER BY COUNT(*) DESC LIMIT 2")]
_c.close()
fake.plan = [("R6人才盘点", ["R6人才盘点-财务部预评估", "R6人才盘点-市场部预评估"], "HIGH"),
             (EST[0], [EST[1]], "HIGH"),
             ("R6销售大赛", ["R6销售大赛复盘会"], "MEDIUM"),
             ("R6人才盘点", ["R6销售大赛"], "HIGH")]   # overlaps the first group -> must be dropped


def t1_ai_groups_and_auto_merge():
    calls0 = fake.calls
    res = PC.run_project_convergence(use_ai=True, force_ai=True)
    assert fake.calls == calls0 + 1, "one model call per AI pass"
    assert status_of(F1) == "merged" and status_of(F2) == "merged" and status_of(UMB) == "active", "HIGH group not auto-merged"
    assert status_of(S1) == "active" and status_of(S2) == "active", "MEDIUM group must not be auto-merged"
    assert len(res["auto_merged"]) == 1 and len(res["pending"]) == 2, (res["auto_merged"], res["pending"])
    c = db()
    rd = c.execute("SELECT reviewer FROM review_decision WHERE candidate_id=?", (res["auto_merged"][0]["candidate_id"],)).fetchone()
    pend = [json.loads(r[0])["payload"]["keeper_project_name"] for r in
            c.execute("SELECT proposed_change FROM candidate WHERE id IN (%s)" % ",".join("?" * len(res["pending"])), res["pending"])]
    c.close()
    assert rd and rd[0] == PC.AUTO_REVIEWER, rd
    assert set(pend) == {EST[0], "R6销售大赛"}, pend
    assert any("pre_auto_convergence" in p.name for p in (TMP / "backups").glob("*")), "no backup before auto-merge"
    return (f"HIGH group auto-merged (reviewer={PC.AUTO_REVIEWER}, backup taken); established pair '{EST[0]}'+'{EST[1]}' "
            f"and MEDIUM group left for review; invalid and overlapping groups dropped")


def t2_signature_gating():
    calls0 = fake.calls
    r1 = PC.run_project_convergence(use_ai=True)
    assert fake.calls == calls0 and r1["ai"]["reason"] == "project_list_unchanged", r1["ai"]
    add_project("R6全新项目", "2026-10-07T00:00:00Z")
    r2 = PC.run_project_convergence(use_ai=True)
    assert fake.calls == calls0 + 1 and r2["ai"]["reason"] == "project_list_changed", r2["ai"]
    r3 = PC.run_project_convergence(use_ai=True, force_ai=True)
    assert fake.calls == calls0 + 2 and r3["ai"]["reason"] == "forced", r3["ai"]
    assert not r3["candidates"], "already decided / pending groups must not be filed again"
    return "model is called only when the project list changed or on a forced check; nothing is filed twice"


def t3_no_model_and_daily_limit():
    settings.deepseek_api_key = ""
    try:
        r = PC.run_project_convergence(use_ai=True, force_ai=True)
        assert r["ai"]["reason"] == "no_model", r["ai"]
    finally:
        settings.deepseek_api_key = "sk-r6-fake"
    settings.daily_call_limit, settings.today_calls_made = 1, 1
    settings.last_call_date = time.strftime("%Y-%m-%d", time.gmtime())
    try:
        r = PC.run_project_convergence(use_ai=True, force_ai=True)
        assert r["ai"]["reason"] == "daily_call_limit_reached", r["ai"]
    finally:
        settings.daily_call_limit, settings.today_calls_made = 0, 0
    return "without a model or over the daily call limit, only the same-name check runs"


def t4_same_name_auto_merge():
    a = add_project("R6同名项目", "2026-03-01T00:00:00Z")
    b = add_project("R6同名项目（项目）", "2026-10-05T00:00:00Z")
    PC.run_project_convergence(use_ai=False)
    assert status_of(b) == "merged" and status_of(a) == "active"
    return "same-name duplicates are merged automatically by the rule pass (no model call)"


def t5_endpoint():
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.core.security import LOCAL_LAUNCH_TOKEN
    client = TestClient(app)
    r = client.post("/v1/local/projects/converge", headers={"X-PAW-Local-Token": LOCAL_LAUNCH_TOKEN, "Host": "127.0.0.1:8800"})
    assert r.status_code == 200 and r.json().get("ok") and "自动收敛" in r.json().get("summary", ""), r.text[:200]
    return f"control-center endpoint works: {r.json()['summary']}"


def t6_no_merge_into_merged_keeper():
    from backend.governance.committer import _execute_project_merge
    c = db()
    try:
        _execute_project_merge(c, {"keeper_project_id": F1, "duplicate_project_ids": [S2]})
        raise AssertionError("merge into a merged keeper was allowed")
    except ValueError:
        pass
    finally:
        c.rollback(); c.close()
    return "merging into a project that was itself merged is refused"


def main():
    tests = [("R6-1", "AI 分组与高置信度自动合并", t1_ai_groups_and_auto_merge),
             ("R6-2", "仅在项目列表变化或强制时调用模型", t2_signature_gating),
             ("R6-3", "无模型/超出每日上限时跳过 AI", t3_no_model_and_daily_limit),
             ("R6-4", "同名项目自动合并", t4_same_name_auto_merge),
             ("R6-5", "控制中心“检查项目收敛”接口", t5_endpoint),
             ("R6-6", "不能并入已被合并的项目", t6_no_merge_into_merged_keeper)]
    log("=" * 60); log("PAW v2.3.8 R6 AI 项目收敛回归测试（隔离临时库，假模型，不调用真实 API）"); log("=" * 60)
    passed = 0
    for tid, title, fn in tests:
        t0 = time.time()
        try:
            detail = fn(); passed += 1
            log(f"[PASS] {tid} {title} ({time.time() - t0:.2f}s): {detail}")
        except Exception as exc:
            log(f"[FAIL] {tid} {title} ({time.time() - t0:.2f}s): {exc}"); log(traceback.format_exc())
    log(f"R6 汇总: 共 {len(tests)} 项，通过: {passed}，失败: {len(tests) - passed}；假模型共被调用 {fake.calls} 次")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    shutil.rmtree(TMP, ignore_errors=True)
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
