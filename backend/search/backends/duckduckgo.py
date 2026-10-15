"""PAW Search Backend - DuckDuckGo Lite.

Features:
- Lightweight HTML-only interface (lite.duckduckgo.com/lite/)
- POST query format
- Clean parsing without JavaScript
- CAPTCHA / Rate limit detection
- Offline fixture parsing support
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


class DuckDuckGoLiteBackend(SearchBackend):
    """DuckDuckGo Search Backend (supports HTML and Lite endpoints)."""

    name: str = "duckduckgo_lite"

    def __init__(self, default_timeout: float = 8.0):
        self.default_timeout = default_timeout

    @staticmethod
    def _decode_ddg_url(raw_url: str) -> str:
        """Decode destination URL from DDG redirect link or schema-relative link."""
        if not raw_url:
            return ""
        if raw_url.startswith("//"):
            raw_url = "https:" + raw_url
        if "uddg=" in raw_url:
            import urllib.parse
            try:
                parsed = urllib.parse.urlparse(raw_url)
                qs = urllib.parse.parse_qs(parsed.query)
                if "uddg" in qs and qs["uddg"]:
                    return qs["uddg"][0]
            except Exception:
                pass
        return raw_url

    @classmethod
    def parse_html(cls, html_content: str, max_results: int = 6) -> List[SearchResult]:
        """Parse DuckDuckGo HTML / Lite result page into normalized SearchResult items."""
        if not html_content or not html_content.strip():
            return []

        lower_html = html_content.lower()
        if (
            "anomaly-modal" in lower_html
            or "select all squares" in lower_html
            or "please verify you are human" in lower_html
            or "Unfortunately, bots use DuckDuckGo too" in html_content
        ):
            raise CaptchaBlockedError("DuckDuckGo presented a bot verification challenge.")

        try:
            doc = lxml.html.fromstring(html_content)
        except Exception as e:
            raise ParseError(f"Failed to parse DDG HTML: {e}") from e

        results: List[SearchResult] = []

        # 1. Try modern html.duckduckgo.com structure
        bodies = doc.xpath('//div[contains(@class, "result__body")]')
        if bodies:
            for b in bodies:
                if len(results) >= max_results:
                    break
                h2_a = b.xpath('.//h2/a')
                if not h2_a:
                    continue
                title = h2_a[0].text_content().strip()
                raw_url = h2_a[0].get("href", "").strip()
                if not title or not raw_url:
                    continue
                clean_url = cls._decode_ddg_url(raw_url)
                snippet = ""
                snips = b.xpath('.//a[contains(@class, "result__snippet")]')
                if snips:
                    snippet = " ".join(snips[0].text_content().split())
                results.append(
                    SearchResult(
                        title=title,
                        snippet=snippet,
                        url=clean_url,
                        source="duckduckgo_lite",
                    )
                )
            if results:
                return results

        # 2. Try lite.duckduckgo.com table structure
        tables = doc.xpath("//table")
        if not tables:
            if "no results" in lower_html or "zero-click" in lower_html:
                return []
            return []

        target_table = tables[-1]
        rows = target_table.xpath(".//tr")

        i = 0
        while i < len(rows) and len(results) < max_results:
            r = rows[i]
            link_nodes = r.xpath('.//a[contains(@class, "result-link")]')
            if link_nodes:
                title = link_nodes[0].text_content().strip()
                raw_url = link_nodes[0].get("href", "").strip()
                snippet = ""

                # Look at next row for snippet
                if i + 1 < len(rows):
                    snip_nodes = rows[i + 1].xpath('.//td[contains(@class, "result-snippet")]')
                    if snip_nodes:
                        snippet = " ".join(snip_nodes[0].text_content().split())
                        i += 1

                if title and raw_url:
                    clean_url = cls._decode_ddg_url(raw_url)
                    results.append(
                        SearchResult(
                            title=title,
                            snippet=snippet,
                            url=clean_url,
                            source="duckduckgo_lite",
                        )
                    )
            i += 1

        return results

    def search(self, request: SearchRequest) -> List[SearchResult]:
        """Execute search query against DuckDuckGo (tries html then lite)."""
        timeout = request.timeout_seconds or self.default_timeout
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "q": request.query,
        }
        client_kwargs = {
            "headers": headers,
            "follow_redirects": True,
            "timeout": timeout,
        }
        if request.proxy:
            client_kwargs["proxy"] = request.proxy

        endpoints = [
            "https://html.duckduckgo.com/html/",
            "https://lite.duckduckgo.com/lite/",
        ]

        last_err: Optional[Exception] = None
        for endpoint in endpoints:
            try:
                with httpx.Client(**client_kwargs) as client:
                    resp = client.post(endpoint, data=data)
                    if resp.status_code == 202:
                        last_err = CaptchaBlockedError(f"DuckDuckGo ({endpoint}) presented bot verification challenge (HTTP 202).")
                        continue
                    if resp.status_code == 429:
                        last_err = RateLimitError(f"DuckDuckGo ({endpoint}) rate limit exceeded (HTTP 429).")
                        continue
                    if resp.status_code >= 500:
                        last_err = NetworkError(f"DuckDuckGo ({endpoint}) server error (HTTP {resp.status_code}).")
                        continue
                    if resp.status_code != 200:
                        last_err = SearchError(f"DuckDuckGo ({endpoint}) returned unexpected status HTTP {resp.status_code}.")
                        continue

                    parsed = self.parse_html(resp.text, max_results=request.max_results)
                    if parsed:
                        return parsed
            except CaptchaBlockedError as e:
                last_err = e
                continue
            except httpx.TimeoutException as e:
                last_err = SearchTimeoutError(f"DuckDuckGo ({endpoint}) timed out after {timeout}s: {e}")
                continue
            except (httpx.ConnectError, httpx.NetworkError) as e:
                last_err = NetworkError(f"DuckDuckGo ({endpoint}) connection error: {e}")
                continue
            except Exception as e:
                last_err = SearchError(f"DuckDuckGo ({endpoint}) unexpected error: {e}")
                continue

        if last_err:
            raise last_err
        return []
