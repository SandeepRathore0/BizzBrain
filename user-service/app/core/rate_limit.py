"""Simple in-process token-bucket rate limiter for sensitive endpoints."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import DefaultDict, Deque, Tuple

from fastapi import Request

from app.core.config import get_settings
from app.core.errors import RateLimitException

# Global in-memory store: key → deque of timestamps
_buckets: DefaultDict[str, Deque[float]] = defaultdict(deque)

# Default limits per endpoint class
_LIMITS: dict[str, Tuple[int, int]] = {
    # (max_requests, window_seconds)
    "login": (10, 60),
    "register": (5, 60),
    "password_reset": (5, 300),
}


def clear_rate_limits() -> None:
    """Clear all rate limit buckets (useful in tests)."""
    _buckets.clear()


def check_rate_limit(key: str, endpoint: str) -> None:
    """
    Raise RateLimitException if `key` has exceeded the allowed rate for `endpoint`.
    Thread-safe enough for single-process; use Redis for multi-worker.
    """
    settings = get_settings()
    if settings.ENVIRONMENT == "test":
        return

    max_req, window = _LIMITS.get(endpoint, (60, 60))
    now = time.monotonic()
    bucket = _buckets[f"{endpoint}:{key}"]
    # Evict old entries outside the window
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    if len(bucket) >= max_req:
        raise RateLimitException("Too many requests. Please wait before retrying.")
    bucket.append(now)


def rate_limit_by_ip(endpoint: str):
    """Returns a FastAPI dependency that rate-limits by client IP."""
    async def _dep(request: Request) -> None:
        client_ip = request.headers.get("X-Forwarded-For") or (
            request.client.host if request.client else "unknown"
        )
        check_rate_limit(client_ip, endpoint)
    return _dep
