"""PAW Search Architecture - Common Contracts & Data Models.

Defines:
- SearchResult: normalized output tuple {title, snippet, url, source, fetched_at}
- SearchRequest: incoming query constraints
- SearchBackend: abstract protocol for search engine implementations
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class SearchResult:
    """Normalized search result item."""
    title: str
    snippet: str
    url: str
    source: str
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SearchRequest:
    """Search request parameter bundle."""
    query: str
    max_results: int = 6
    timeout_seconds: float = 8.0
    proxy: Optional[str] = None


class SearchBackend(ABC):
    """Abstract interface for modular search backends."""

    name: str = "base"

    @abstractmethod
    def search(self, request: SearchRequest) -> List[SearchResult]:
        """Execute search query and return normalized results.

        Must raise specific exceptions on failure (e.g., CaptchaBlockedError, RateLimitError, ParseError).
        """
        raise NotImplementedError


class SearchError(Exception):
    """Base exception for search failures."""
    pass


class CaptchaBlockedError(SearchError):
    """Triggered when search engine displays CAPTCHA or security challenge."""
    pass


class RateLimitError(SearchError):
    """Triggered when engine limits request frequency."""
    pass


class SearchTimeoutError(SearchError):
    """Triggered when search request exceeds timeout limit."""
    pass


class ParseError(SearchError):
    """Triggered when HTML structure changed and results could not be parsed."""
    pass


class NetworkError(SearchError):
    """Triggered when DNS, connection, or SSL fails."""
    pass
