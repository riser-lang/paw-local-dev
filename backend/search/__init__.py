"""PAW Search Architecture Package."""
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
from backend.search.service import SearchService

__all__ = [
    "SearchResult",
    "SearchRequest",
    "SearchBackend",
    "SearchError",
    "CaptchaBlockedError",
    "RateLimitError",
    "SearchTimeoutError",
    "ParseError",
    "NetworkError",
    "SearchService",
]
