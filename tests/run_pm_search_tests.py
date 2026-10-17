"""C-11 installed-app acceptance for filename and special-character search."""
import hashlib
import json
import os
import time
from pathlib import Path

import httpx
from docx import Document

from tests.run_pm_tests import (
    api, BASE, running, INSTALL_DIR, clean_and_record, silent_install,
    configure_mock_settings, poll_candidates_from_scanner,
)
from tests.run_e2e_tests import parse_candidate_types, timed_answer


FILENAME = "星火导师计划_方案_v2.docx"
BODY = "星火导师计划方案说明。已完成第一阶段验证，下一步整理交付清单。"


def prepare_search_document(case_id):
    directory = Path(os.environ["TEMP"]) / ("PAW_PM_C11_" + case_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / FILENAME
    document = Document()
    document.core_properties.title = FILENAME
    document.add_paragraph(BODY)
    document.save(path)
    configure_mock_settings(directory)
    api("POST", "/v1/local/scanner/start", {
        "directory": str(directory), "force_restart": True,
    })
    candidates = poll_candidates_from_scanner()
    _, _, _, knowledge_id = parse_candidate_types(candidates)
    if not knowledge_id:
        raise RuntimeError("扫描完成但没有知识候选")
    accepted = api("POST", "/v1/web/brain/review/batch_accept", {
        "candidate_ids": [knowledge_id], "reviewer": "LocalTester",
    })
    if accepted.get("failed_count") != 0:
        raise RuntimeError("知识候选没有经过审核流程成功核准")
    # Wait until the derived retrieval index reflects the approved document.
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        result = httpx.get(BASE + "/v1/web/workspace/projection/knowledge-search",
                           params={"q": FILENAME, "limit": 20}, timeout=20).json()
        if any(item.get("title") == FILENAME or BODY[:16] in item.get("snippet", "")
               for item in result.get("items", [])):
            return result
        time.sleep(0.25)
    return result


def run_c11(report, installer):
    from playwright.sync_api import sync_playwright

    with report.case("PM-S1-01", "资料页完整文件名搜索") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer)[2], INSTALL_DIR)
        with running():
            directory = Path(os.environ["TEMP"]) / ("PAW_PM_C11_" + c.id)
            directory.mkdir(parents=True, exist_ok=True)
            document = Document()
            document.core_properties.title = FILENAME
            document.add_paragraph(BODY)
            path = directory / FILENAME
            document.save(path)
            api("POST", "/v1/web/workspace/sources/register", {
                "title": FILENAME,
                "canonical_location": str(path),
                "source_origin": "office_sync",
                "content_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
                "file_ext": path.suffix,
                "source_kind": "document",
                "canonical_state": "canonical",
            })
            sources = api("GET", "/v1/web/workspace/sources?limit=500")
            items = sources.get("items", [])
            found = any(item.get("title") == FILENAME for item in items)
            print("C-11 source registry search evidence", json.dumps({
                "count": len(items),
                "matching_titles": [item.get("title") for item in items
                                    if item.get("title") == FILENAME],
            }, ensure_ascii=False))
            c.check("full_filename_search_found", found, True)
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 900})
                    page.goto(BASE + "/app/files", wait_until="networkidle")
                    search = page.get_by_placeholder("搜索文件名或分类", exact=True)
                    search.fill(FILENAME)
                    page.get_by_text(FILENAME, exact=True).first.wait_for(timeout=10000)
                    rendered = page.locator(".source-grid").inner_text()
                    c.check("full_filename_search_rendered", FILENAME in rendered, True)
                finally:
                    browser.close()

    with report.case("PM-S1-02", "特殊字符检索不报错且页面明确反馈") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer)[2], INSTALL_DIR)
        with running():
            prepare_search_document(c.id)
            query = '星火"导师"(计划)*:方案-v2.docx'
            response = httpx.get(BASE + "/v1/web/workspace/projection/knowledge-search",
                                 params={"q": query, "limit": 20}, timeout=20)
            c.check("special_query_api_success", response.status_code, 200)
            payload = response.json()
            c.check("special_query_response_shape", isinstance(payload.get("items"), list), True)
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 900})
                    page.goto(BASE + "/app/knowledge", wait_until="networkidle")
                    search = page.get_by_role("searchbox", name="搜索正式知识")
                    search.fill(query)
                    search.press("Enter")
                    page.get_by_text("没有找到匹配的当前有效知识。", exact=True).wait_for(timeout=10000)
                    c.check("special_query_page_feedback", "没有找到匹配的当前有效知识。" in page.locator("body").inner_text(), True)
                finally:
                    browser.close()

    with report.case("PM-S1-03", "AI 助手回答包含完整文件名来源") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer)[2], INSTALL_DIR)
        with running():
            prepare_search_document(c.id)
            session = api("POST", "/v1/web/agy/chat", {"mode": "chat"})["session_id"]
            answer = timed_answer(session, FILENAME + " 里写了什么？", c.id)
            c.check("assistant_response_not_empty", bool(str(answer.get("response") or "").strip()), True)
            sources = answer.get("sources") or answer.get("source_refs") or answer.get("evidence") or []
            c.check("assistant_source_contains_full_filename",
                    FILENAME in json.dumps(sources, ensure_ascii=False), True)
