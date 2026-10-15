"""PAW Search Backend - Microsoft Bing (Domestic / Global direct).

Features:
- Direct HTTP GET with honest desktop User-Agent
- Real URL unmasking from Bing /ck/a click tracking links
- CAPTCHA / Bot detection
- Offline fixture parsing support
"""
from __future__ import annotations

import base64
import logging
import urllib.parse
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


class BingSearchBackend(SearchBackend):
    """Microsoft Bing Search Backend."""

    name: str = "bing"

    def __init__(self, default_timeout: float = 8.0):
        self.default_timeout = default_timeout

    @staticmethod
    def _unmask_bing_url(href: str) -> str:
        """Decode destination URL from Bing /ck/a redirect tracking link."""
        if not href or "/ck/a?" not in href:
            return href
        try:
            parsed = urllib.parse.urlparse(href)
            qs = urllib.parse.parse_qs(parsed.query)
            u_val = qs.get("u", [""])[0]
            if u_val.startswith("a1"):
                b64_str = u_val[2:]
                padding = len(b64_str) % 4
                if padding:
                    b64_str += "=" * (4 - padding)
                decoded = base64.b64decode(b64_str).decode("utf-8", errors="ignore")
                if decoded.startswith("http://") or decoded.startswith("https://"):
                    return decoded
        except Exception:
            pass
        return href

    @classmethod
    def parse_html(cls, html_content: str, max_results: int = 6) -> List[SearchResult]:
        """Parse Bing HTML result page into normalized SearchResult items."""
        if not html_content or not html_content.strip():
            return []

        # Check for CAPTCHA / bot challenge markers
        lower_html = html_content.lower()
        if (
            "b_challenge" in lower_html
            or "please solve the challenge" in lower_html
            or "waf-challenge" in lower_html
            or "verification" in lower_html and "captcha" in lower_html
        ):
            raise CaptchaBlockedError("Bing presented a challenge or verification page.")

        try:
            doc = lxml.html.fromstring(html_content)
        except Exception as e:
            raise ParseError(f"Failed to parse HTML document: {e}") from e

        results: List[SearchResult] = []
        algo_nodes = doc.xpath('//li[contains(@class, "b_algo")]')

        for node in algo_nodes:
            if len(results) >= max_results:
                break

            h2_links = node.xpath(".//h2/a")
            if not h2_links:
                continue

            title = h2_links[0].text_content().strip()
            raw_url = h2_links[0].get("href", "").strip()
            if not title or not raw_url:
                continue

            clean_url = cls._unmask_bing_url(raw_url)

            snippet_nodes = (
                node.xpath('.//div[contains(@class, "b_caption")]//p')
                or node.xpath(".//p")
            )
            snippet = ""
            if snippet_nodes:
                snippet = " ".join(snippet_nodes[0].text_content().split())

            results.append(
                SearchResult(
                    title=title,
                    snippet=snippet,
                    url=clean_url,
                    source="bing",
                )
            )

        return results

    def search(self, request: SearchRequest) -> List[SearchResult]:
        """Execute search query against Bing."""
        timeout = request.timeout_seconds or self.default_timeout
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        params = {
            "q": request.query,
            "setmkt": "zh-CN",
            "setlang": "zh-Hans",
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
                resp = client.get("https://www.bing.com/search", params=params)

                if resp.status_code == 429:
                    raise RateLimitError("Bing rate limit exceeded (HTTP 429).")
                if resp.status_code >= 500:
                    raise NetworkError(f"Bing server error (HTTP {resp.status_code}).")
                if resp.status_code != 200:
                    raise SearchError(f"Bing returned unexpected status HTTP {resp.status_code}.")

                return self.parse_html(resp.text, max_results=request.max_results)

        except httpx.TimeoutException as e:
            raise SearchTimeoutError(f"Bing search timed out after {timeout}s: {e}") from e
        except (httpx.ConnectError, httpx.NetworkError) as e:
            raise NetworkError(f"Bing network connection error: {e}") from e
        except (CaptchaBlockedError, RateLimitError, SearchTimeoutError, ParseError):
            raise
        except Exception as e:
            raise SearchError(f"Bing search unexpected error: {e}") from e
