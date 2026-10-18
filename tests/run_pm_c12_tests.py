"""C-12 installed-app acceptance and baseline negative controls."""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from tests.run_pm_tests import (
    api, BASE, running, project, INSTALL_DIR, clean_and_record, silent_install,
)
from tests.run_pm_versions_tests import import_doc, family
from tests.run_e2e_tests import parse_candidate_types, timed_answer


PLAN_V1 = "目标：第一期培养新任导师 30 名。生效日期：2026年9月1日。"
PLAN_V2 = "目标调整：第一期培养新任导师 40 名。生效日期：2026年10月8日。本版替代 v1。"
WEEKLY = "进度：已招募 26 名导师。风险：两个校区尚未提名导师人选。下一步：完成剩余 14 名导师招募。"
DETAIL = "C12 正文样本：青禾试点已完成首轮访谈，下一步整理访谈纪要。"


def c12_project():
    return api("POST", "/v1/web/workspace/projects", {"name": "青禾项目"})["id"]


def prepare_versioned_project(case):
    pid = c12_project()
    first, proposals = import_doc(case, "青禾计划_v1.docx", "青禾项目。" + PLAN_V1)
    candidate = parse_candidate_types(proposals)[0]
    if candidate:
        api("POST", "/v1/web/brain/review/candidates/" + candidate + "/accept", {})
    second, _ = import_doc(case, "青禾计划_v2.docx", "青禾项目。" + PLAN_V2)
    return pid, first, second


def current_target_visible(pid):
    from playwright.sync_api import sync_playwright

    try:
        data = api("GET", "/v1/pm/view/projects/" + pid)
        body = json.dumps(data, ensure_ascii=False)
        api_expected = "40" in body and "2026-10-08" in body and "待确认：目标由 30 改为 40" in body
        print("C-12 target API evidence", json.dumps({
            "api_expected": api_expected,
            "pending_targets": data.get("pending_targets"),
        }, ensure_ascii=False))
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.goto(BASE + "/app/projects", wait_until="networkidle")
                page.get_by_role("link", name="项目", exact=True).click()
                page.locator(".pm-project-page h1").filter(has_text="青禾项目").wait_for(timeout=15000)
                rendered = page.locator("body").inner_text()
                print("C-12 target page evidence", json.dumps({
                    "target_40": "40" in rendered,
                    "effective_date": "2026年10月8日" in rendered,
                    "pending_change": "待确认：目标由 30 改为 40" in rendered,
                    "progress_not_target": "已招募 26 名" not in rendered,
                }, ensure_ascii=False))
                page.screenshot(path="screenshots/screen_PM_U1_01.png", full_page=True)
                return api_expected and "40" in rendered and "2026年10月8日" in rendered \
                    and "待确认：目标由 30 改为 40" in rendered and "已招募 26 名" not in rendered
            finally:
                browser.close()
    except Exception as exc:
        print("C-12 target visibility error", type(exc).__name__)
        return False


def page_text(url):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(BASE + url, wait_until="networkidle")
            page.locator("body").wait_for()
            return page, browser, page.inner_text("body")
        except Exception:
            browser.close()
            raise


def no_internal_terms_visible(case):
    from playwright.sync_api import sync_playwright

    import_doc(case, "青禾项目周报.docx", "青禾项目。" + WEEKLY)
    pid = c12_project()
    forbidden = re.compile(r"AUTO_WRITE|REVIEW_REQUIRED|data create|create todo|pending|\bcreate\b|\bupdate\b|\btodo\b|\bmetric\b", re.I)
    rendered = []
    route_names = ["/app/review", "/app/todos", "/app/pm/projects/青禾项目"]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            for route in ("/app/review", "/app/todos", "/app/pm/projects/" + pid):
                page.goto(BASE + route, wait_until="networkidle")
                rendered.append(page.locator("body").inner_text())
                route_names[len(rendered) - 1] = route
            page.screenshot(path="screenshots/screen_PM_U3_01.png", full_page=True)
        finally:
            browser.close()
    session = api("POST", "/v1/web/agy/chat", {"mode": "chat"})["session_id"]
    answer = timed_answer(session, "青禾项目周报记录了什么？", case)
    rendered.append(str(answer.get("response") or ""))
    matches = [{"route": route_names[index] if index < len(route_names) else "assistant",
                "terms": list(dict.fromkeys(match.group(0).lower() for match in forbidden.finditer(text)))}
               for index, text in enumerate(rendered) if forbidden.search(text)]
    if matches:
        print("C-12 visible internal-term check", json.dumps(matches, ensure_ascii=False))
    return not matches


def scan_metric_candidate(case):
    from openpyxl import Workbook
    from tests.run_pm_tests import poll_candidates_from_scanner

    directory = Path(__import__("os").environ["TEMP"]) / ("PAW_C12_METRIC_" + case)
    directory.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "青禾项目目标"
    sheet.append(["指标", "目标值", "当前进度"])
    sheet.append(["导师培养人数", 40, 26])
    workbook.save(directory / "青禾项目量化数据.xlsx")
    api("POST", "/v1/local/scanner/start", {"directory": str(directory), "force_restart": True})
    poll_candidates_from_scanner()
    candidates = api("GET", "/v1/web/brain/review/candidates?status=pending&limit=200").get("items", [])
    observed_types = []
    for item in candidates:
        candidate_id = item.get("id") or item.get("candidate_id")
        if not candidate_id:
            continue
        detail = api("GET", "/v1/web/brain/review/candidates/" + str(candidate_id))
        change = detail.get("proposed_change") or {}
        if isinstance(change, str):
            try:
                change = json.loads(change)
            except ValueError:
                change = {}
        target_type = change.get("target_type") if isinstance(change, dict) else None
        observed_types.append({
            "candidate_type": item.get("candidate_type"),
            "target_type": target_type,
        })
        if str(item.get("candidate_type") or "").lower() in {"data", "metric", "data_asset"} \
                or str(target_type or "").lower() in {"data", "metric", "data_asset"}:
            return {**item, "presentation": detail.get("presentation"), "proposed_change": change}
    print("C-12 data candidate evidence", json.dumps({
        "candidate_count": len(candidates), "observed_types": observed_types,
    }, ensure_ascii=False))
    return None


def run_c12(report, installer):
    from playwright.sync_api import sync_playwright

    ids = ["PM-U1-01", "PM-U1-02", "PM-U1-03", "PM-U2-01", "PM-U2-02", "PM-U3-01", "PM-U3-02", "PM-U4-01"]
    for case in ids:
        with report.case(case, "v2.5.0-rc1 真机验收问题：" + case) as c:
            c.fresh_install(clean_and_record, lambda: silent_install(installer)[2], INSTALL_DIR)
            with running():
                if case in {"PM-U1-01", "PM-U1-03", "PM-U4-01"}:
                    pid, first, second = prepare_versioned_project(case)
                    if case == "PM-U1-01":
                        c.check("current_target_visible", current_target_visible(pid), True)
                    elif case == "PM-U1-03":
                        session = api("POST", "/v1/web/agy/chat", {"mode": "chat", "project_id": pid})["session_id"]
                        answer = timed_answer(session, "青禾项目计划目标是多少？", case)
                        sources = answer.get("sources") or answer.get("evidence") or answer.get("source_refs") or []
                        c.check("assistant_uses_current_version", "40" in str(answer.get("response") or "") and "青禾计划_v2.docx" in json.dumps(sources, ensure_ascii=False), True)
                    else:
                        import_doc(case, "青禾项目周报.docx", "青禾项目。" + WEEKLY)
                        session = api("POST", "/v1/web/agy/chat", {"mode": "chat", "project_id": pid})["session_id"]
                        answer = timed_answer(session, "青禾项目后续最好如何优化？", case)
                        text = str(answer.get("response") or "")
                        c.check("advice_not_refused", "建议" in text and bool(answer.get("sources") or answer.get("evidence") or answer.get("source_refs")), True)
                elif case == "PM-U1-02":
                    pid, _, _ = prepare_versioned_project(case)
                    import_doc(case, "青禾项目周报.docx", "青禾项目。" + WEEKLY)
                    vm = api("GET", "/v1/pm/view/projects/" + pid)
                    rendered = json.dumps(vm, ensure_ascii=False)
                    c.check("risk_shown_as_top_issue", "校区" in rendered and "关键问题尚未记录" not in rendered, True)
                elif case == "PM-U2-01":
                    from tests.run_pm_search_tests import scan_file_for_sources, FILENAME, BODY
                    items = scan_file_for_sources(case)
                    source = next((item for item in items if item.get("title") == FILENAME), None)
                    md_dir = Path(__import__("os").environ["TEMP"]) / ("PAW_PM_C11_" + case)
                    md_path = md_dir / (Path(FILENAME).stem + ".md")
                    # C-11's scan helper registers a docx. Add a scanned Markdown
                    # file through the same watcher and assert the source details API.
                    md_path.write_text(DETAIL, encoding="utf-8")
                    api("POST", "/v1/local/scanner/start", {"directory": str(md_dir), "force_restart": True})
                    from tests.run_pm_tests import poll_candidates_from_scanner
                    poll_candidates_from_scanner()
                    files = api("GET", "/v1/web/workspace/sources?limit=500")["items"]
                    markdown = next((item for item in files if item.get("title") == md_path.name), None)
                    markdown_content = api("GET", "/v1/web/workspace/sources/" + markdown["id"]).get("content") if markdown else None
                    docx_content = api("GET", "/v1/web/workspace/sources/" + source["id"]).get("content") if source else None
                    print("C-12 source detail evidence", json.dumps({
                        "docx_source_found": bool(source),
                        "markdown_source_found": bool(markdown),
                        "docx_content_length": len(str(docx_content or "")),
                        "markdown_content_length": len(str(markdown_content or "")),
                        "docx_body_found": BODY in str(docx_content),
                        "markdown_body_found": DETAIL in str(markdown_content),
                    }, ensure_ascii=False))
                    c.check("source_body_readable", bool(source and markdown and DETAIL in str(markdown_content)
                            and BODY in str(docx_content) and "暂时无法读取正文" not in str(markdown_content)), True)
                    with sync_playwright() as p:
                        browser = p.chromium.launch(headless=False)
                        try:
                            page = browser.new_page(viewport={"width": 1440, "height": 900})
                            page.goto(BASE + "/app/files", wait_until="networkidle")
                            search = page.get_by_placeholder("搜索文件名或分类", exact=True)
                            search.fill(FILENAME)
                            page.get_by_role("button", name=re.compile(re.escape(FILENAME))).first.click()
                            page.locator(".source-content-card pre").wait_for()
                            rendered_docx = page.locator(".source-content-card pre").inner_text()
                            page.goto(BASE + "/app/files", wait_until="networkidle")
                            search = page.get_by_placeholder("搜索文件名或分类", exact=True)
                            search.fill(md_path.name)
                            page.get_by_role("button", name=re.compile(re.escape(md_path.name))).first.click()
                            page.locator(".source-content-card pre").wait_for()
                            rendered_md = page.locator(".source-content-card pre").inner_text()
                            c.check("source_body_rendered", BODY in rendered_docx and DETAIL in rendered_md
                                    and "暂时无法读取正文" not in page.inner_text("body"), True)
                        finally:
                            browser.close()
                elif case == "PM-U2-02":
                    from tests.run_pm_search_tests import prepare_search_document, FILENAME
                    prepare_search_document(case)
                    knowledge = api("GET", "/v1/web/workspace/knowledge?limit=100").get("items", [])
                    details = []
                    for item in knowledge[:5]:
                        kid = item.get("id")
                        if not kid:
                            continue
                        response = httpx.get(BASE + "/v1/web/workspace/knowledge/" + kid, timeout=20)
                        projection = httpx.get(BASE + "/v1/web/workspace/projection/knowledge/" + kid, timeout=20)
                        details.append(response.status_code == 200 and projection.status_code == 200)
                        if not details[-1]:
                            try:
                                error_body = response.json()
                            except ValueError:
                                error_body = {}
                            errors = error_body.get("detail", []) if isinstance(error_body, dict) else []
                            safe_errors = [
                                {key: entry.get(key) for key in ("loc", "type", "msg") if key in entry}
                                for entry in errors if isinstance(entry, dict)
                            ]
                            print("C-12 knowledge detail endpoint evidence", json.dumps({
                                "knowledge_id": kid, "detail_status": response.status_code,
                                "projection_status": projection.status_code,
                                "detail_errors": safe_errors,
                            }, ensure_ascii=False))
                    session = api("POST", "/v1/web/agy/chat", {"mode": "chat"})["session_id"]
                    answer = timed_answer(session, FILENAME + " 里写了什么？", case)
                    refs = answer.get("sources") or []
                    source_details = [httpx.get(BASE + "/v1/web/workspace/knowledge/" + str(ref.get("id")), timeout=20).status_code == 200
                                      for ref in refs if isinstance(ref, dict) and ref.get("id") in {item.get("id") for item in knowledge}]
                    if not all(details) or not all(source_details):
                        print("C-12 knowledge detail evidence", json.dumps({
                            "knowledge_count": len(knowledge), "checked_details": details,
                            "source_refs": refs, "source_detail_checks": source_details,
                        }, ensure_ascii=False))
                    c.check("knowledge_detail_opens", bool(details) and all(details) and all(source_details), True)
                elif case == "PM-U3-01":
                    c.check("no_internal_terms_visible", no_internal_terms_visible(case), True)
                else:
                    candidate = scan_metric_candidate(case)
                    c.check("data_metric_card_available", bool(candidate), True)
                    with sync_playwright() as p:
                        browser = p.chromium.launch(headless=False)
                        try:
                            page = browser.new_page(viewport={"width": 1440, "height": 1000})
                            page.goto(BASE + "/app/review", wait_until="networkidle")
                            display_title = str((candidate.get("presentation") or {}).get("display_title") or "")
                            card = page.locator(".review-card").filter(has_text=display_title).first if display_title else page.locator(".review-card").first
                            card.click()
                            title = page.get_by_role("textbox", name="标题", exact=True)
                            project_name = page.get_by_role("textbox", name="项目名称", exact=True)
                            c.check("missing_fields_have_inputs", title.is_visible() and project_name.is_visible(), True)
                            if not title.input_value():title.fill("青禾项目指标")
                            if not project_name.input_value():project_name.fill("青禾项目")
                            page.get_by_role("button", name="保存草稿", exact=True).click()
                            page.get_by_text("草稿已保存", exact=False).wait_for()
                            page.get_by_role("button", name="接受", exact=True).click()
                            page.get_by_text("已提交", exact=False).wait_for()
                            c.check("every_card_resolvable", "标题或项目名称不能为空" not in page.inner_text("body"), True)
                            page.screenshot(path="screenshots/screen_" + case.replace("-", "_") + ".png", full_page=True)
                        finally:
                            browser.close()


def run_c12_negative(report, baseline_installer):
    controls = [
        ("NC-PM-U1", "PM-U1-01", "current_target_visible"),
        ("NC-PM-U2", "PM-U2-01", "source_body_readable"),
        ("NC-PM-U3", "PM-U3-01", "no_internal_terms_visible"),
        ("NC-PM-U4", "PM-U4-01", "advice_not_refused"),
    ]
    for control, runs, expected in controls:
        with report.case(control, "v2.5.0-rc1 安装包负对照：" + runs) as c:
            c.fresh_install(clean_and_record, lambda: silent_install(baseline_installer)[2], INSTALL_DIR)
            with running():
                # Execute the same product path and named assertion on the baseline.
                if runs == "PM-U1-01":
                    pid, _, _ = prepare_versioned_project(runs)
                    passed = current_target_visible(pid)
                    c.check(expected, passed, True)
                elif runs == "PM-U2-01":
                    from tests.run_pm_search_tests import scan_file_for_sources, FILENAME
                    items = scan_file_for_sources(runs)
                    source = next((item for item in items if item.get("title") == FILENAME), None)
                    body = None
                    if source:
                        try:
                            body = api("GET", "/v1/web/workspace/sources/" + source["id"]).get("content")
                        except Exception:
                            body = None
                    passed = bool(source and body and BODY in str(body) and "暂时无法读取正文" not in str(body))
                    c.check(expected, passed, True)
                elif runs == "PM-U3-01":
                    passed = no_internal_terms_visible(runs)
                    c.check(expected, passed, True)
                else:
                    pid = c12_project()
                    import_doc(runs, "青禾项目周报.docx", "青禾项目。" + WEEKLY)
                    session = api("POST", "/v1/web/agy/chat", {"mode": "chat", "project_id": pid})["session_id"]
                    answer = timed_answer(session, "青禾项目后续最好如何优化？", runs)
                    sources = answer.get("sources") or answer.get("evidence") or answer.get("source_refs") or []
                    passed = "建议" in str(answer.get("response") or "") and bool(sources)
                    c.check(expected, passed, True)
