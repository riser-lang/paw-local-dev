"""C-11 installed acceptance: search with extensions and special characters."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import quote

from tests.paw_case import Report
from tests.run_pm_tests import (
    api, BASE, running, project, read_rows,
    INSTALL_DIR, clean_and_record, silent_install, configure_mock_settings,
)
from tests.run_e2e_tests import timed_answer, get_db_path, poll_candidates_from_scanner
from tests.run_pm_frontend_tests import stable_page, capture

logger = logging.getLogger("pm.search.tests")

# A filename that contains more than three CJK characters plus an extension:
# exactly the shape that used to reach FTS5 verbatim and match nothing.
DOC_TITLE = "项目周报_v2.docx"
DOC_BODY = (
    "冲刺项目。\n"
    "进度与里程碑：原型已完成，联调已启动。\n"
    "下一步：整理验收记录，准备复核。\n"
)


def search(query: str, limit: int = 20) -> dict:
    return api("GET", "/v1/web/workspace/projection/knowledge-search?q="
               + quote(query, safe="") + "&limit=" + str(limit))


def import_document(case: str, title: str, body: str) -> str:
    """Drop one .docx into a watched folder and scan it once."""
    from docx import Document

    directory = Path(os.environ["TEMP"]) / ("PAW_PM_SEARCH_" + case)
    directory.mkdir(parents=True, exist_ok=True)
    document = Document()
    document.add_paragraph(body)
    document.core_properties.title = title
    path = directory / title
    document.save(path)

    configure_mock_settings(directory)
    api("POST", "/v1/local/scanner/start",
        {"directory": str(directory), "force_restart": True})
    from tests.run_e2e_tests import poll_candidates_from_scanner
    candidates = poll_candidates_from_scanner()
    for candidate in candidates:
        if candidate.get("status") == "pending":
            try:
                api("POST", f"/v1/web/brain/review/candidates/{candidate['id']}/accept", {})
            except Exception:
                pass
    time.sleep(1)
    return title


def _titles(payload: dict) -> list[str]:
    return [str(i.get("title") or "") for i in (payload.get("items") or [])]


def run_c11(report: Report, installer) -> None:
    from playwright.sync_api import sync_playwright, expect

    # ------------------------------------------------------------------
    # PM-S1-01: a complete filename (with dot and extension) finds the doc
    # ------------------------------------------------------------------
    with report.case("PM-S1-01",
                     "导入带扩展名的文档后，搜索完整文件名能找到该文档") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer)[2], INSTALL_DIR)
        with running():
            pid = project()
            import_document("PM-S1-01", DOC_TITLE, DOC_BODY)

            payload = search(DOC_TITLE)
            c.check("search_returns_items", bool(payload.get("items")), True)
            c.check("full_filename_search_found", DOC_TITLE in _titles(payload), True)
            c.check("search_query_echoed", payload.get("query"), DOC_TITLE)

            # The same query without the extension must also still work.
            bare = search("项目周报")
            c.check("bare_name_search_found", DOC_TITLE in _titles(bare), True)

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    stable_page(page, BASE + "/app/knowledge")
                    box = page.get_by_role("searchbox")
                    if box.count() == 0:
                        box = page.get_by_placeholder("搜索")
                    box.first.fill(DOC_TITLE)
                    box.first.press("Enter")
                    time.sleep(1.5)
                    body = page.inner_text("body")
                    c.check("page_shows_search_result", DOC_TITLE in body, True)
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    capture(c, page, 1440, "_search")
                    page.set_viewport_size({"width": 390, "height": 900})
                    capture(c, page, 390, "_search")
                finally:
                    browser.close()

    # ------------------------------------------------------------------
    # PM-S1-02: special characters never raise; the page shows no error
    # ------------------------------------------------------------------
    with report.case("PM-S1-02",
                     "搜索词含特殊字符时不报错，正常返回或明确显示没有找到") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer)[2], INSTALL_DIR)
        with running():
            pid = project()
            import_document("PM-S1-02", DOC_TITLE, DOC_BODY)

            hostile = ['"', "(", ")", "*", ":", "-", "a*b", "时间:10:00",
                       "v2.0-rc1", '"未闭合', "(((", "%_\\", "项目周报_v2.docx"]
            ok = []
            for query in hostile:
                try:
                    payload = search(query)
                except Exception as exc:  # a raised error is exactly the defect
                    c.check("no_error_for_" + query[:6], f"raised {type(exc).__name__}", "no error")
                    continue
                ok.append(query)
                c.check("items_shape_for_" + query[:6], isinstance(payload.get("items"), list), True)
            c.check("hostile_queries_all_answered", len(ok), len(hostile))

            # A query with only punctuation must be an empty, not an error.
            empty = search("...")
            c.check("punctuation_only_returns_empty", empty.get("items"), [])

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    stable_page(page, BASE + "/app/knowledge")
                    box = page.get_by_role("searchbox")
                    if box.count() == 0:
                        box = page.get_by_placeholder("搜索")
                    box.first.fill('项目周报_v2.docx"(*):-')
                    box.first.press("Enter")
                    time.sleep(1.5)
                    body = page.inner_text("body")
                    c.check("page_shows_no_error_text",
                            any(k in body for k in ("出错", "错误", "Error", "500")), False)
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    capture(c, page, 1440, "_special")
                    page.set_viewport_size({"width": 390, "height": 900})
                    capture(c, page, 390, "_special")
                finally:
                    browser.close()

    # ------------------------------------------------------------------
    # PM-S1-03: the assistant cites the file when asked what it says
    # ------------------------------------------------------------------
    with report.case("PM-S1-03",
                     "问助手某个完整文件名里写了什么时，回答引用了该文件") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer)[2], INSTALL_DIR)
        with running():
            pid = project()
            import_document("PM-S1-03", DOC_TITLE, DOC_BODY)

            question = f"{DOC_TITLE} 里写了什么？"
            session = api("POST", "/v1/web/agy/chat", {"mode": "chat", "project_id": pid})["session_id"]
            answer = timed_answer(session, question, "PM-S1-03")
            text = str(answer.get("response") or "")
            sources = [str(s) for s in (answer.get("sources") or [])]
            c.check("assistant_answer_not_empty", bool(text.strip()), True)
            c.check("assistant_cites_source_doc",
                    any(DOC_TITLE in s for s in sources), True)

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    stable_page(page, BASE + "/app/agnes")
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    capture(c, page, 1440, "_assistant")
                    page.set_viewport_size({"width": 390, "height": 900})
                    capture(c, page, 390, "_assistant")
                finally:
                    browser.close()


def run_c11_negative(report: Report, installer) -> None:
    """2.3.9 must fail on the full-filename check and nothing else."""
    with report.case("NC-PM-S1", "2.3.9 上完整文件名搜索不到的负对照") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer)[2], INSTALL_DIR)
        with running():
            project()
            import_document("NC-PM-S1", DOC_TITLE, DOC_BODY)
            titles = _titles(search(DOC_TITLE))
            c.check("full_filename_search_found", DOC_TITLE in titles, True)
