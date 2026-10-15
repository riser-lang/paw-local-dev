"""PAW R2 Requirement W1 - Live Search Backend Feasibility & Accessibility Probe.

Runs >= 20 diverse test queries across 4 candidate backends:
1. Bing CN (https://cn.bing.com/search)
2. Baidu (https://www.baidu.com/s)
3. Sogou (https://www.sogou.com/web)
4. DuckDuckGo Lite (https://lite.duckduckgo.com/lite/)

Measures:
- HTTP Status Code
- Round-trip Latency (ms)
- Parsed Results Count (title + snippet + url)
- Anti-bot / Captcha / Blocking Detection
- Output formatted report & raw HTML fixtures for parsers
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
import urllib.parse
import httpx
import lxml.html

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "logs"
FIXTURES_DIR = ROOT / "tests" / "fixtures" / "search_html"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

RAW_LOG_FILE = LOGS_DIR / "w1_backend_probe.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(RAW_LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("paw_search_probe")

# Standard honest browser User-Agent
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 PAW-Personal-Work-OS/2.3"

TEST_QUERIES = [
    # 1. 业务与方法类 (5)
    "数字化转型方法论与实施路径",
    "企业知识库搭建最佳实践",
    "OKR与KPI的区别和结合使用",
    "敏捷项目管理燃尽图怎么看",
    "Python并发编程asyncio与线程池",
    # 2. 公司与产品类 (5)
    "腾讯控股最新财报营收情况",
    "微软Copilot商业化定价策略",
    "DeepSeek开源模型架构创新点",
    "阿里云通义千问最新大模型版本",
    "华为鸿蒙HarmonyOS NEXT生态进展",
    # 3. 新闻与宏观类 (5)
    "2026年全球科技趋势预测",
    "近期国内新能源汽车出口数据",
    "最新个人所得税专项附加扣除标准",
    "人工智能法案最新监管动态",
    "国内算力中心建设最新政策",
    # 4. 英文与技术类 (5)
    "sqlite fts5 full text search rank bm25",
    "fastapi background tasks vs celery",
    "python lxml html xpath tutorial",
    "uvicorn single process worker concurrency",
    "windows dpapi cryptprotectdata python example",
]


def parse_bing_html(html_text: str) -> list[dict]:
    results = []
    try:
        doc = lxml.html.fromstring(html_text)
        # Bing search results are typically in <li class="b_algo">
        items = doc.xpath('//li[contains(@class, "b_algo")]')
        for item in items:
            title_nodes = item.xpath('.//h2/a')
            if not title_nodes:
                continue
            title = title_nodes[0].text_content().strip()
            url = title_nodes[0].get("href", "").strip()
            
            # Snippets in <p> or <div class="b_caption">
            caption_nodes = item.xpath('.//div[contains(@class, "b_caption")]//p | .//p')
            snippet = caption_nodes[0].text_content().strip() if caption_nodes else ""
            if title and url.startswith("http"):
                results.append({
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    "source": "bing"
                })
    except Exception as e:
        logger.debug(f"Bing parse error: {e}")
    return results


def parse_baidu_html(html_text: str) -> list[dict]:
    results = []
    try:
        doc = lxml.html.fromstring(html_text)
        # Baidu search results in <div class="result c-container ...">
        items = doc.xpath('//div[contains(@class, "result") and contains(@class, "c-container")]')
        for item in items:
            title_nodes = item.xpath('.//h3/a')
            if not title_nodes:
                continue
            title = title_nodes[0].text_content().strip()
            url = title_nodes[0].get("href", "").strip()
            # Baidu snippet usually in content-right or span
            snippet_nodes = item.xpath('.//div[contains(@class, "c-font-normal")] | .//span[contains(@class, "content-right_8Zs40")] | .//div[contains(@class, "c-span-last")]')
            snippet = snippet_nodes[0].text_content().strip() if snippet_nodes else ""
            if title and url:
                results.append({
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    "source": "baidu"
                })
    except Exception as e:
        logger.debug(f"Baidu parse error: {e}")
    return results


def parse_sogou_html(html_text: str) -> list[dict]:
    results = []
    try:
        doc = lxml.html.fromstring(html_text)
        # Sogou results in <div class="vrwrap"> or <div class="rb">
        items = doc.xpath('//div[contains(@class, "vrwrap")] | //div[contains(@class, "rb")]')
        for item in items:
            title_nodes = item.xpath('.//h3/a')
            if not title_nodes:
                continue
            title = title_nodes[0].text_content().strip()
            url = title_nodes[0].get("href", "").strip()
            if url.startswith("/"):
                url = "https://www.sogou.com" + url
            snippet_nodes = item.xpath('.//p[contains(@class, "txt-info")] | .//div[contains(@class, "space-txt")]')
            snippet = snippet_nodes[0].text_content().strip() if snippet_nodes else ""
            if title and url:
                results.append({
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    "source": "sogou"
                })
    except Exception as e:
        logger.debug(f"Sogou parse error: {e}")
    return results


def parse_ddg_lite_html(html_text: str) -> list[dict]:
    results = []
    try:
        doc = lxml.html.fromstring(html_text)
        # DDG Lite uses table rows: <td class="result-link"> followed by <td class="result-snippet">
        links = doc.xpath('//a[contains(@class, "result-link")]')
        snippets = doc.xpath('//td[contains(@class, "result-snippet")]')
        for idx, a in enumerate(links):
            title = a.text_content().strip()
            url = a.get("href", "").strip()
            snippet = snippets[idx].text_content().strip() if idx < len(snippets) else ""
            if title and url:
                results.append({
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    "source": "duckduckgo_lite"
                })
    except Exception as e:
        logger.debug(f"DDG Lite parse error: {e}")
    return results


def test_backend_query(client: httpx.Client, backend_id: str, query: str) -> dict:
    t0 = time.perf_counter()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
    }
    
    url = ""
    method = "GET"
    data = None

    if backend_id == "bing":
        url = f"https://www.bing.com/search?q={urllib.parse.quote_plus(query)}&setmkt=zh-CN&setlang=zh-Hans"
    elif backend_id == "baidu":
        url = f"https://www.baidu.com/s?wd={urllib.parse.quote_plus(query)}&ie=utf-8"
    elif backend_id == "sogou":
        url = f"https://www.sogou.com/web?query={urllib.parse.quote_plus(query)}"
    elif backend_id == "duckduckgo_lite":
        url = "https://lite.duckduckgo.com/lite/"
        method = "POST"
        data = {"q": query}
    else:
        raise ValueError(f"Unknown backend: {backend_id}")

    res_meta = {
        "backend": backend_id,
        "query": query,
        "success": False,
        "status_code": 0,
        "latency_ms": 0,
        "results_count": 0,
        "blocked": False,
        "error": None,
        "samples": [],
    }

    try:
        if method == "GET":
            resp = client.get(url, headers=headers, timeout=8.0, follow_redirects=True)
        else:
            resp = client.post(url, data=data, headers=headers, timeout=8.0, follow_redirects=True)
        
        latency = round((time.perf_counter() - t0) * 1000)
        res_meta["status_code"] = resp.status_code
        res_meta["latency_ms"] = latency

        html_text = resp.text

        # Anti-bot / Captcha detection
        if "captcha" in html_text.lower() or "verify" in html_text.lower() or "验证码" in html_text or "网络不给力，请稍后重试" in html_text or "安全验证" in html_text or "百度安全验证" in html_text:
            res_meta["blocked"] = True
            res_meta["error"] = "CAPTCHA_OR_SECURITY_CHECK"
            return res_meta

        if resp.status_code == 200:
            if backend_id == "bing":
                parsed = parse_bing_html(html_text)
            elif backend_id == "baidu":
                parsed = parse_baidu_html(html_text)
            elif backend_id == "sogou":
                parsed = parse_sogou_html(html_text)
            elif backend_id == "duckduckgo_lite":
                parsed = parse_ddg_lite_html(html_text)
            else:
                parsed = []

            res_meta["results_count"] = len(parsed)
            res_meta["samples"] = parsed[:3]
            if len(parsed) > 0:
                res_meta["success"] = True
            else:
                res_meta["error"] = "EMPTY_PARSE_OR_STRUCTURE_CHANGED"
                # Save first empty html for inspection
                sample_p = FIXTURES_DIR / f"{backend_id}_empty_sample.html"
                if not sample_p.exists():
                    sample_p.write_text(html_text[:50000], encoding="utf-8")
        else:
            res_meta["error"] = f"HTTP_{resp.status_code}"

    except Exception as e:
        latency = round((time.perf_counter() - t0) * 1000)
        res_meta["latency_ms"] = latency
        res_meta["error"] = type(e).__name__

    return res_meta


def run_probe():
    logger.info("=" * 80)
    logger.info("   PAW R2 Requirement W1 - 候选搜索后端实机网络可用性评估 (20 Queries)")
    logger.info("=" * 80)

    backends = ["bing", "baidu", "sogou", "duckduckgo_lite"]
    backend_stats: Dict[str, Any] = {
        b: {
            "total": 0,
            "success": 0,
            "latencies": [],
            "blocked_count": 0,
            "errors": {},
            "total_results": 0,
        }
        for b in backends
    }

    raw_records = []

    # Use HTTP/1.1 client with reasonable pool
    with httpx.Client(http2=False, timeout=8.0) as client:
        # First save 1 normal HTML fixture for each backend for parser unit testing
        for b in backends:
            try:
                logger.info(f"正在抓取 {b} 的基准 HTML 夹具 (Fixture)...")
                q = "人工智能最新发展现状"
                if b == "bing":
                    u = f"https://www.bing.com/search?q={urllib.parse.quote_plus(q)}"
                    r = client.get(u, headers={"User-Agent": USER_AGENT}, timeout=8.0)
                elif b == "baidu":
                    u = f"https://www.baidu.com/s?wd={urllib.parse.quote_plus(q)}"
                    r = client.get(u, headers={"User-Agent": USER_AGENT}, timeout=8.0)
                elif b == "sogou":
                    u = f"https://www.sogou.com/web?query={urllib.parse.quote_plus(q)}"
                    r = client.get(u, headers={"User-Agent": USER_AGENT}, timeout=8.0)
                else:
                    u = "https://lite.duckduckgo.com/lite/"
                    r = client.post(u, data={"q": q}, headers={"User-Agent": USER_AGENT}, timeout=8.0)
                
                if r.status_code == 200:
                    (FIXTURES_DIR / f"{b}_sample.html").write_text(r.text, encoding="utf-8")
                    logger.info(f"  * {b} 样例 HTML 夹具已保存: {FIXTURES_DIR / f'{b}_sample.html'} ({len(r.text)} 字符)")
            except Exception as e:
                logger.warning(f"  * 保存 {b} 样例失败: {e}")

        logger.info("\n开始对 4 个后端依次执行 20 条混合查询测试...\n")

        for idx, query in enumerate(TEST_QUERIES, 1):
            logger.info(f"[{idx:02d}/20] 测试查询词: '{query}'")
            for b in backends:
                res = test_backend_query(client, b, query)
                raw_records.append(res)
                stats = backend_stats[b]
                stats["total"] += 1
                if res["success"]:
                    stats["success"] += 1
                    stats["latencies"].append(res["latency_ms"])
                    stats["total_results"] += res["results_count"]
                if res["blocked"]:
                    stats["blocked_count"] += 1
                if res["error"]:
                    stats["errors"][res["error"]] = stats["errors"].get(res["error"], 0) + 1

                status_str = "SUCCESS" if res["success"] else f"FAIL({res['error']})"
                first_title = res["samples"][0]["title"][:30] if res["samples"] else "None"
                logger.info(f"  -> [{b:15s}] {status_str:15s} | 耗时={res['latency_ms']:4d}ms | 结果数={res['results_count']:2d} | 样例='{first_title}'")
                time.sleep(0.3)  # Gentle spacing to avoid self-triggering rate limits
            logger.info("-" * 75)

    # ── Summary Report Generation ─────────────────────────────────────────────
    logger.info("\n" + "=" * 80)
    logger.info("   【W1 候选搜索后端实机实测统计汇总表】")
    logger.info("=" * 80)
    logger.info(f"{'后端标识':18s} | {'成功率':10s} | {'平均耗时':10s} | {'拦截/阻断':10s} | {'平均结果数':10s} | {'主要失败原因'}")
    logger.info("-" * 80)

    summary_table = []
    for b in backends:
        s = backend_stats[b]
        rate = f"{(s['success'] / s['total'] * 100):.1f}%" if s['total'] else "0%"
        avg_lat = f"{(sum(s['latencies']) / len(s['latencies'])):.0f}ms" if s['latencies'] else "N/A"
        avg_res = f"{(s['total_results'] / s['success']):.1f}条" if s['success'] else "0条"
        err_str = ", ".join(f"{k}:{v}" for k, v in s['errors'].items()) or "无异常"
        logger.info(f"{b:18s} | {rate:10s} | {avg_lat:10s} | {s['blocked_count']:8d}次 | {avg_res:10s} | {err_str}")
        summary_table.append({
            "backend": b,
            "total_queries": s["total"],
            "success_rate": rate,
            "avg_latency": avg_lat,
            "blocked_count": s["blocked_count"],
            "avg_results": avg_res,
            "errors": s["errors"],
        })

    # Save summary JSON
    summary_file = ROOT / "data" / "w1_backend_probe_summary.json"
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump({"summary": summary_table, "raw_records": raw_records}, f, indent=2, ensure_ascii=False)
    logger.info(f"\n实测统计与原始记录已持久化至: {summary_file}")
    logger.info(f"完整实测日志路径: {RAW_LOG_FILE}")


if __name__ == "__main__":
    run_probe()
