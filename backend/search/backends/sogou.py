"""PAW Search Backend - Sogou Search.

Features:
- Direct HTTP GET with honest desktop User-Agent
- Strict Anti-Bot / CAPTCHA detection (e.g. sogou.com/antispider)
- Per Specification §1: strictly NO captcha cracking, NO UA rotation
- Raises CaptchaBlockedError to trigger honest fallback or degradation
"""
from __future__ import annotations

import logging
from typing import List, Optional

import httpx
import lxml.html

from backend.search.contract import (
    CaptchaBlockedError,
    NetworkError,
    ParseError,
    RateLimitError,
    SearchBackend,
    SearchError,
    SearchRequest,
    SearchResult,
    SearchTimeoutError,
)

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
)


class SogouSearchBackend(SearchBackend):
    """Sogou Search Backend."""

    name: str = "sogou"

    def __init__(self, default_timeout: float = 8.0):
        self.default_timeout = default_timeout

    @classmethod
    def parse_html(cls, html_content: str, max_results: int = 6) -> List[SearchResult]:
        """Parse Sogou HTML result page."""
        if not html_content or not html_content.strip():
            return []

        lower_html = html_content.lower()
        if (
            "antispider" in lower_html
            or "captcha" in lower_html
            or "访问过于频繁" in html_content
            or "请输入验证码" in html_content
        ):
            raise CaptchaBlockedError("Sogou presented antispider challenge / CAPTCHA.")

        try:
            doc = lxml.html.fromstring(html_content)
        except Exception as e:
            raise ParseError(f"Failed to parse Sogou HTML: {e}") from e

        results: List[SearchResult] = []
        wrappers = doc.xpath('//div[contains(@class, "vrwrap")] | //div[contains(@class, "rb")]')

        for w in wrappers:
            if len(results) >= max_results:
                break
            h3 = w.xpath('.//h3//a')
            if not h3:
                continue
            title = h3[0].text_content().strip()
            url = h3[0].get("href", "").strip()
            if not title or not url:
                continue

            snippet_nodes = (
                w.xpath('.//p[contains(@class, "space-txt")]')
                or w.xpath('.//div[contains(@class, "space-txt")]')
                or w.xpath('.//p')
            )
            snippet = ""
            if snippet_nodes:
                snippet = " ".join(snippet_nodes[0].text_content().split())

            results.append(
                SearchResult(
                    title=title,
                    snippet=snippet,
                    url=url,
                    source="sogou",
                )
            )

        return results

    def search(self, request: SearchRequest) -> List[SearchResult]:
        """Execute search query against Sogou."""
        timeout = request.timeout_seconds or self.default_timeout
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        params = {
            "query": request.query,
        }
        client_kwargs = {
            "headers": headers,
            "follow_redirects": True,
            "timeout": timeout,
        }
        if request.proxy:
            client_kwargs["proxy"] = request.proxy

        try:
            with httpx.Client(**client_kwargs) as client:
                resp = client.get("https://www.sogou.com/web", params=params)

                final_url = str(resp.url)
                if "antispider" in final_url:
                    raise CaptchaBlockedError("Sogou redirected to antispider challenge.")

                if resp.status_code == 429:
                    raise RateLimitError("Sogou rate limit exceeded (HTTP 429).")
                if resp.status_code >= 500:
                    raise NetworkError(f"Sogou server error (HTTP {resp.status_code}).")
                if resp.status_code != 200:
                    raise SearchError(f"Sogou returned unexpected status HTTP {resp.status_code}.")

                return self.parse_html(resp.text, max_results=request.max_results)

        except httpx.TimeoutException as e:
            raise SearchTimeoutError(f"Sogou timed out after {timeout}s: {e}") from e
        except (httpx.ConnectError, httpx.NetworkError) as e:
            raise NetworkError(f"Sogou connection error: {e}") from e
        except (CaptchaBlockedError, RateLimitError, SearchTimeoutError, ParseError):
            raise
        except Exception as e:
            raise SearchError(f"Sogou unexpected error: {e}") from e
