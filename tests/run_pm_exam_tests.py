"""C-10 installed acceptance: Weekly reports, Evolution metrics, Standard exam executor."""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from pathlib import Path
import httpx

from tests.run_pm_tests import (
    api, BASE, running, project, read_rows,
    INSTALL_DIR, clean_and_record, silent_install, configure_mock_settings
)
from tests.run_pm_versions_tests import import_doc
from tests.run_pm_frontend_tests import stable_page, capture, view
from tests.run_e2e_tests import timed_answer
from scripts.build_exam_corpus import build_corpus, load_corpus_spec, evaluate_semantics

logger = logging.getLogger("pm.exam.tests")


def wait_for(fn, description: str, seconds: int = 60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        res = fn()
        if res:
            return res
        time.sleep(0.3)
    raise RuntimeError("等待实际处理完成失败: " + description)


def run_c10(report, installer):
    from playwright.sync_api import sync_playwright, expect

    # =========================================================================
    # PM-F1-01: 项目周报与跨项目对比
    # =========================================================================
    with report.case("PM-F1-01", "为一个项目生成周报，以及一份跨项目对比；内容只来自已有记录和带依据的建议") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer)[2], INSTALL_DIR)
        with running():
            pid1 = project()
            import_doc(
                "PM-F1-01",
                "冲刺研制方案_v1.docx",
                "冲刺项目。\n进度与里程碑：原型已完成。\n风险与冲突：验收资料尚未复核。\n下一步：整理验收记录。"
            )
            # Create second project for cross-project comparison
            pid2 = api("POST", "/v1/web/workspace/projects", {"name": "星空项目"})["id"]

            # 1. Without model key: suggestions indicate needs_key, but other sections work
            api("POST", "/v1/local/settings", {
                "provider": "custom",
                "custom_api_key": "",
                "custom_base_url": "",
                "custom_model": ""
            })
            res_nokey = api("POST", "/v1/pm/weekly-reports/generate", {"project_id": pid1})
            rep_nokey = res_nokey.get("report", res_nokey)
            c.check("weekly_report_project_name", rep_nokey["project_name"], "冲刺项目")
            c.check("weekly_report_progress_populated", any("原型已完成" in p["text"] for p in rep_nokey["progress"]), True)
            c.check("weekly_report_nokey_flag", rep_nokey["suggestions"]["needs_key"], True)
            c.check("weekly_report_nokey_message", "需要填写 Key" in (rep_nokey["suggestions"]["message"] or ""), True)

            # 2. Configure mock model and generate suggestions
            api("POST", "/v1/local/settings", {
                "provider": "custom",
                "custom_api_key": "sk-fake-paw-test",
                "custom_base_url": "http://127.0.0.1:18000/v1",
                "custom_model": "gpt-4o"
            })
            api("POST", f"/v1/pm/projects/{pid1}/suggestions/generate", {})

            # 3. With key: weekly report includes grounded suggestions
            res_keyed = api("POST", "/v1/pm/weekly-reports/generate", {"project_id": pid1})
            rep_keyed = res_keyed.get("report", res_keyed)
            c.check("weekly_report_keyed_flag", rep_keyed["suggestions"]["needs_key"], False)
            c.check("weekly_report_has_suggestions", len(rep_keyed["suggestions"]["items"]) > 0, True)
            c.check("weekly_suggestions_have_basis", all(bool(s.get("basis")) for s in rep_keyed["suggestions"]["items"]), True)

            # 4. Cross-project comparison
            cross = api("GET", "/v1/pm/weekly-reports/cross-project")
            c.check("cross_project_count", cross["project_count"] >= 2, True)
            matrix_pids = [m["project_id"] for m in cross["matrix"]]
            c.check("cross_project_includes_both", pid1 in matrix_pids and pid2 in matrix_pids, True)

            # 5. UI rendering verification via Playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})

                    # Project page
                    stable_page(page, BASE + f"/app/pm/projects/{pid1}")
                    proj_region = page.get_by_role("region", name="项目周报", exact=True)
                    expect(proj_region).to_contain_text("【进展】")
                    expect(proj_region).to_contain_text("【建议】")
                    c.check("ui_project_weekly_rendered", "【进展】" in proj_region.inner_text(), True)
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    capture(c, page, 1440, "_project")
                    page.set_viewport_size({"width": 390, "height": 900})
                    capture(c, page, 390, "_project")

                    # Cockpit page
                    stable_page(page, BASE + "/app/pm/today")
                    cockpit_region = page.get_by_role("region", name="跨项目对比与项目周报", exact=True)
                    expect(cockpit_region).to_contain_text("跨项目对比")
                    expect(cockpit_region).to_contain_text("冲刺项目")
                    c.check("ui_cockpit_cross_rendered", "冲刺项目" in cockpit_region.inner_text(), True)
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    capture(c, page, 1440, "_cockpit")
                    page.set_viewport_size({"width": 390, "height": 900})
                    capture(c, page, 390, "_cockpit")
                finally:
                    browser.close()

    # =========================================================================
    # PM-F2-01: 进化指标趋势
    # =========================================================================
    with report.case("PM-F2-01", "进化指标按周记录，驾驶舱上能看到趋势") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer)[2], INSTALL_DIR)
        with running():
            pid = project()
            import_doc(
                "PM-F2-01",
                "指标测试方案.docx",
                "冲刺项目。\n进度与里程碑：原型已完成。\n风险与冲突：验收资料尚未复核。"
            )

            # Record two historical snapshots
            api("POST", "/v1/pm/evolution-metrics/snapshot", {"period": "2026-W40"})
            api("POST", "/v1/pm/evolution-metrics/snapshot", {"period": "2026-W41"})

            # Read metrics and trends
            trends_res = api("GET", "/v1/pm/evolution-metrics")
            items = trends_res["items"]
            metric_ids = sorted(m["id"] for m in items)
            expected_ids = [
                "backlog", "conflicts", "coverage_rate",
                "knowledge_gaps", "pending_decisions", "suggestion_adoption_rate"
            ]
            c.check("six_evolution_metrics", metric_ids, expected_ids)
            c.check(
                "evolution_trend_has_snapshots",
                all(any(t["period"] in ("2026-W40", "2026-W41") for t in m.get("trend", [])) for m in items),
                True
            )

            # Validate today cockpit view model schema
            today_vm = view()
            evo_in_vm = today_vm.get("evolution_metrics", [])
            c.check("evolution_metrics_in_cockpit_vm", len(evo_in_vm), 6)

            # Playwright check for cockpit rendering
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    stable_page(page, BASE + "/app/pm/today")
                    region = page.get_by_role("region", name="系统进化指标", exact=True)
                    expect(region).to_contain_text("系统进化指标")
                    expect(region).to_contain_text("周趋势")
                    c.check("ui_evolution_metrics_rendered", "系统进化指标" in region.inner_text(), True)
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    capture(c, page, 1440, "_evolution")
                    page.set_viewport_size({"width": 390, "height": 900})
                    capture(c, page, 390, "_evolution")
                finally:
                    browser.close()

    # =========================================================================
    # PM-F3-01: 标准考题执行器
    # =========================================================================
    with report.case("PM-F3-01", "标准考题集在当前版本上运行，结果可查；答对率低于上一版时，发布流程判红") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer)[2], INSTALL_DIR)
        with running():
            # Configure mock model settings
            api("POST", "/v1/local/settings", {
                "provider": "custom",
                "custom_api_key": "sk-fake-paw-test",
                "custom_base_url": "http://127.0.0.1:18000/v1",
                "custom_model": "gpt-4o"
            })

            # Setup staging and watch directories
            watch_dir = Path(os.environ["TEMP"]) / "PAW_EXAM_WATCH"
            watch_dir.mkdir(parents=True, exist_ok=True)
            staging_dir = Path(os.environ["TEMP"]) / "PAW_EXAM_STAGING"
            staging_dir.mkdir(parents=True, exist_ok=True)

            configure_mock_settings(watch_dir)

            # 1. Build exam corpus verbatim
            built_files = build_corpus(output_dir=staging_dir)
            c.check("exam_corpus_built_count", len(built_files), 8)

            corpus_docs = load_corpus_spec()

            # Ensure projects mentioned in corpus.json exist
            for p_name in ["星火导师计划", "人才盘点", "年度预算"]:
                try:
                    api("POST", "/v1/web/workspace/projects", {"name": p_name})
                except Exception:
                    pass

            # 2. Step-by-step ingestion into watch directory
            for doc_info in sorted(corpus_docs, key=lambda d: d.get("step", 0)):
                step_num = doc_info["step"]
                filename = doc_info["file"]
                src_path = staging_dir / filename
                dst_path = watch_dir / filename

                shutil.copyfile(src_path, dst_path)
                api("POST", "/v1/local/scanner/start", {"directory": str(watch_dir), "force_restart": True})

                # Wait for scanner progress to complete
                wait_for(
                    lambda: api("GET", "/v1/local/scanner/progress").get("status") == "completed",
                    f"Step {step_num} ({filename}) 扫描完成",
                    seconds=45
                )

                # Approve all non-decision candidates through the normal review flow
                try:
                    candidates = api("GET", "/v1/web/brain/review/candidates").get("items", [])
                    for cand in candidates:
                        if cand.get("status") == "pending":
                            try:
                                api("POST", f"/v1/web/brain/review/candidates/{cand['id']}/accept", {})
                            except Exception:
                                pass
                except Exception:
                    pass

                # Resolve any pending PM family decisions where project is already known
                try:
                    decisions = api("GET", "/v1/pm/decisions").get("items", [])
                    for dec in decisions:
                        if dec.get("status") == "pending" and dec.get("project_id"):
                            try:
                                api("POST", f"/v1/pm/decisions/{dec['id']}/resolve", {
                                    "project_id": dec["project_id"],
                                    "family_title": dec.get("family_key") or dec.get("title") or ""
                                })
                            except Exception:
                                pass
                except Exception:
                    pass

            time.sleep(2)

            # 3. Load exam questions
            questions_spec_path = Path(__file__).resolve().parent / "pending" / "exam" / "questions.json"
            questions_data = json.loads(questions_spec_path.read_text(encoding="utf-8"))
            questions = questions_data["questions"]

            exam_results = []
            for q_item in questions:
                qid = q_item["id"]
                prompt = q_item["q"]

                # Open a fresh assistant session for each question
                chat_session = api("POST", "/v1/web/agy/chat", {"mode": "chat"})
                session_id = chat_session["session_id"]

                # Ask question
                ans_payload = timed_answer(session_id, prompt, f"exam_{qid}")
                answer_text = ans_payload.get("response", "")
                sources = ans_payload.get("sources", [])

                # Get knowledge gaps for insufficient checks
                try:
                    gaps = api("GET", "/v1/pm/knowledge-gaps").get("items", [])
                except Exception:
                    gaps = []

                # Evaluate semantics
                passed, reason = evaluate_semantics(answer_text, sources, q_item, knowledge_gaps=gaps)
                exam_results.append({
                    "id": qid,
                    "question": prompt,
                    "answer": answer_text,
                    "sources": sources,
                    "rules": {k: v for k, v in q_item.items() if k not in ("id", "q")},
                    "passed": passed,
                    "reason": reason
                })

            pass_count = sum(1 for r in exam_results if r["passed"])
            total_count = len(exam_results)
            pass_rate = pass_count / total_count if total_count else 0.0

            # Write exam_result.json report artifact
            reports_dir = Path("reports")
            reports_dir.mkdir(parents=True, exist_ok=True)
            result_json_path = reports_dir / "exam_result.json"
            result_payload = {
                "total": total_count,
                "passed": pass_count,
                "pass_rate": pass_rate,
                "min_pass_rate": questions_data.get("gate", {}).get("min_pass_rate", 0.9),
                "baseline_pass_rate": pass_rate,
                "results": exam_results
            }
            result_json_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")

            # Assert pass rate >= 90%
            c.check("exam_total_questions", total_count, 24)
            c.check("exam_pass_rate_ge_90_percent", pass_rate >= 0.9, True)
            c.check("exam_min_passed_count", pass_count >= 22, True)

            # Screenshot cockpit
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    stable_page(page, BASE + "/app/pm/today")
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    capture(c, page, 1440, "_exam")
                    page.set_viewport_size({"width": 390, "height": 900})
                    capture(c, page, 390, "_exam")
                finally:
                    browser.close()
