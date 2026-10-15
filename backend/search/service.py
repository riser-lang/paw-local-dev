"""PAW Search Service - Orchestrator, Fallback, Rate Limit & Cache.

Manages:
- Ordered backend fallback: Bing -> DuckDuckGo Lite -> Baidu/Sogou
- 10-minute query result caching
- Daily search counter and limit
- SSRF-safe page content extraction
- Health check / Doctor integration
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from backend.search.backends.baidu import BaiduSearchBackend
from backend.search.backends.bing import BingSearchBackend
from backend.search.backends.duckduckgo import DuckDuckGoLiteBackend
from backend.search.backends.sogou import SogouSearchBackend
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
from backend.search.ssrf import fetch_and_clean_text

logger = logging.getLogger(__name__)


def extract_query_keywords(query: str) -> List[str]:
    """Extract key search tokens (alphanumeric words and Chinese terms/ngrams)."""
    import re
    tokens = []
    raw_tokens = re.findall(r'[a-zA-Z0-9_\u4e00-\u9fff]+', query.lower())
    for t in raw_tokens:
        if re.search(r'[\u4e00-\u9fff]', t):
            tokens.append(t)
            if len(t) > 2:
                for i in range(len(t) - 1):
                    tokens.append(t[i:i+2])
        else:
            if len(t) >= 2:
                tokens.append(t)
    return list(dict.fromkeys(tokens))


def calculate_relevance(query: str, result: SearchResult) -> float:
    """Calculate relevance score between 0.0 and 1.0 based on keyword overlap."""
    keywords = extract_query_keywords(query)
    if not keywords:
        return 1.0
    text = f"{result.title} {result.snippet} {result.url}".lower()
    matches = sum(1 for kw in keywords if kw in text)
    score = matches / len(keywords)
    return round(min(1.0, score), 3)


class SearchService:
    """Unified Search Orchestrator with Fallback and Safeguards."""

    def __init__(
        self,
        backends: Optional[List[SearchBackend]] = None,
        cache_ttl_seconds: float = 600.0,
        daily_limit: int = 50,
        proxy: Optional[str] = None,
    ):
        self.backends: List[SearchBackend] = backends or [
            DuckDuckGoLiteBackend(),
            BingSearchBackend(),
            BaiduSearchBackend(),
            SogouSearchBackend(),
        ]
        self.cache_ttl_seconds = cache_ttl_seconds
        self.daily_limit = daily_limit
        self.proxy = proxy

        self._lock = threading.RLock()
        self._cache: Dict[str, Tuple[float, List[SearchResult]]] = {}
        self._daily_date: str = date.today().isoformat()
        self._daily_count: int = 0

    def _normalize_query(self, query: str) -> str:
        return " ".join(query.strip().lower().split())

    def _check_and_increment_daily(self) -> Tuple[bool, int, int]:
        """Check if daily quota is reached and increment counter. Returns (is_allowed, count, limit)."""
        today = date.today().isoformat()
        with self._lock:
            if self._daily_date != today:
                self._daily_date = today
                self._daily_count = 0

            if self.daily_limit > 0 and self._daily_count >= self.daily_limit:
                return False, self._daily_count, self.daily_limit

            self._daily_count += 1
            return True, self._daily_count, self.daily_limit

    def get_daily_status(self) -> Dict[str, Any]:
        """Get current daily search usage."""
        today = date.today().isoformat()
        with self._lock:
            if self._daily_date != today:
                self._daily_date = today
                self._daily_count = 0
            return {
                "date": self._daily_date,
                "used": self._daily_count,
                "limit": self.daily_limit,
                "remaining": max(0, self.daily_limit - self._daily_count) if self.daily_limit > 0 else -1,
            }

    def search(
        self,
        query: str,
        max_results: int = 6,
        timeout_seconds: float = 8.0,
        bypass_cache: bool = False,
    ) -> Dict[str, Any]:
        """Perform search across backends with fallback and caching.

        Returns:
            dict: {
                "success": bool,
                "query": str,
                "results": List[SearchResult],
                "backend_used": Optional[str],
                "from_cache": bool,
                "daily_remaining": int,
                "error": Optional[str],
                "fallback_trace": List[str],
            }
        """
        norm_query = self._normalize_query(query)
        if not norm_query:
            return {
                "success": False,
                "query": query,
                "results": [],
                "backend_used": None,
                "from_cache": False,
                "daily_remaining": 0,
                "error": "Query string is empty",
                "fallback_trace": [],
            }

        # 1. Check cache
        now = time.time()
        if not bypass_cache:
            with self._lock:
                if norm_query in self._cache:
                    cached_time, cached_results = self._cache[norm_query]
                    if now - cached_time < self.cache_ttl_seconds:
                        logger.info(f"[SearchService] Cache hit for '{norm_query}'")
                        status = self.get_daily_status()
                        return {
                            "success": True,
                            "query": query,
                            "results": cached_results[:max_results],
                            "backend_used": "cache",
                            "from_cache": True,
                            "daily_remaining": status["remaining"],
                            "error": None,
                            "fallback_trace": ["cache_hit"],
                        }

        # 2. Check daily quota
        allowed, count, limit = self._check_and_increment_daily()
        if not allowed:
            logger.warning(f"[SearchService] Daily search limit ({limit}) reached!")
            return {
                "success": False,
                "query": query,
                "results": [],
                "backend_used": None,
                "from_cache": False,
                "daily_remaining": 0,
                "error": f"Daily search limit reached ({limit}/{limit}).",
                "fallback_trace": ["daily_limit_exceeded"],
            }

        # 3. Iterate through backends with fallback
        request = SearchRequest(
            query=query,
            max_results=max_results,
            timeout_seconds=timeout_seconds,
            proxy=self.proxy,
        )

        fallback_trace: List[str] = []
        last_error: Optional[str] = None

        deadline=time.monotonic()+timeout_seconds
        for backend in self.backends:
            remaining=deadline-time.monotonic()
            if remaining <= 0:
                fallback_trace.append('total_search_deadline')
                break
            request = SearchRequest(query=query,max_results=max_results,timeout_seconds=remaining,proxy=self.proxy)
            backend_name = backend.name
            try:
                logger.info(f"[SearchService] Trying backend '{backend_name}' for query '{query}'")
                t0 = time.time()
                results = backend.search(request)
                elapsed_ms = int((time.time() - t0) * 1000)

                if results:
                    scored_results = []
                    for r in results:
                        score = calculate_relevance(query, r)
                        scored_results.append((score, r))

                    # Filter out complete irrelevant noise (score == 0.0)
                    positive_scored = [item for item in scored_results if item[0] > 0.0]
                    if positive_scored:
                        positive_scored.sort(key=lambda x: x[0], reverse=True)
                        final_results = [x[1] for x in positive_scored]
                        top_score = positive_scored[0][0]
                    else:
                        # All results had 0 keyword overlap -> honeypot or spam
                        fallback_trace.append(f"{backend_name}:IRRELEVANT(all 0.0 relevance, discarded {len(results)} items)")
                        logger.warning(f"[SearchService] Backend '{backend_name}' returned only irrelevant/honeypot results for '{query}', falling back...")
                        continue

                    fallback_trace.append(f"{backend_name}:OK({len(final_results)} items, {elapsed_ms}ms, top_rel={top_score})")
                    with self._lock:
                        self._cache[norm_query] = (now, final_results)
                    status = self.get_daily_status()
                    return {
                        "success": True,
                        "query": query,
                        "results": final_results,
                        "backend_used": backend_name,
                        "from_cache": False,
                        "daily_remaining": status["remaining"],
                        "error": None,
                        "fallback_trace": fallback_trace,
                    }
                else:
                    fallback_trace.append(f"{backend_name}:EMPTY({elapsed_ms}ms)")
                    logger.info(f"[SearchService] Backend '{backend_name}' returned 0 results, trying next...")

            except CaptchaBlockedError as e:
                err_msg = f"{backend_name}:CAPTCHA_BLOCKED({e})"
                fallback_trace.append(err_msg)
                last_error = str(e)
                logger.warning(f"[SearchService] {err_msg}")
            except RateLimitError as e:
                err_msg = f"{backend_name}:RATE_LIMITED({e})"
                fallback_trace.append(err_msg)
                last_error = str(e)
                logger.warning(f"[SearchService] {err_msg}")
            except SearchTimeoutError as e:
                err_msg = f"{backend_name}:TIMEOUT({e})"
                fallback_trace.append(err_msg)
                last_error = str(e)
                logger.warning(f"[SearchService] {err_msg}")
            except NetworkError as e:
                err_msg = f"{backend_name}:NETWORK_ERROR({e})"
                fallback_trace.append(err_msg)
                last_error = str(e)
                logger.warning(f"[SearchService] {err_msg}")
            except Exception as e:
                err_msg = f"{backend_name}:ERROR({type(e).__name__}: {e})"
                fallback_trace.append(err_msg)
                last_error = str(e)
                logger.warning(f"[SearchService] {err_msg}")

        # All backends failed or returned empty
        logger.error(f"[SearchService] All backends failed for query '{query}': {fallback_trace}")
        status = self.get_daily_status()
        return {
            "success": False,
            "query": query,
            "results": [],
            "backend_used": None,
            "from_cache": False,
            "daily_remaining": status["remaining"],
            "error": last_error or "All search backends returned empty results or failed.",
            "fallback_trace": fallback_trace,
        }

    def fetch_page_content(self, url: str) -> Optional[str]:
        """Fetch and sanitize webpage content with Anti-SSRF protection."""
        return fetch_and_clean_text(url, proxy=self.proxy)

    def check_health(self) -> Dict[str, Any]:
        """Probe backends and return health diagnostics for Doctor."""
        probe_query = "Python 官网"
        expected_indicator = "python.org"
        req = SearchRequest(query=probe_query, max_results=3, timeout_seconds=6.0, proxy=self.proxy)
        backend_statuses = []

        overall_healthy = False
        for backend in self.backends[:2]:
            t0 = time.time()
            try:
                res = backend.search(req)
                elapsed_ms = int((time.time() - t0) * 1000)
                has_expected = any(expected_indicator in r.url.lower() or "python" in r.title.lower() for r in res)
                status_str = "HEALTHY" if has_expected else "DEGRADED"
                if has_expected:
                    overall_healthy = True
                backend_statuses.append({
                    "name": backend.name,
                    "status": status_str,
                    "latency_ms": elapsed_ms,
                    "result_count": len(res),
                    "indicator_matched": has_expected,
                    "error": None if has_expected else f"Expected '{expected_indicator}' not found in results",
                })
            except Exception as e:
                elapsed_ms = int((time.time() - t0) * 1000)
                backend_statuses.append({
                    "name": backend.name,
                    "status": "UNHEALTHY",
                    "latency_ms": elapsed_ms,
                    "result_count": 0,
                    "indicator_matched": False,
                    "error": f"{type(e).__name__}: {e}",
                })

        return {
            "healthy": overall_healthy,
            "probe_query": probe_query,
            "backends": backend_statuses,
            "daily_usage": self.get_daily_status(),
        }
