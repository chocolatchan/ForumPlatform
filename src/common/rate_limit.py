"""In-memory sliding window rate limiter for abuse control on sensitive endpoints.

Design requirements:
- Bounded memory with automated cleanup.
- Configurable window seconds and max requests.
- Returns canonical HTTP 429 response when limit exceeded.
- Identifies clients by trusted client host or key (e.g. IP + endpoint or user identifier).
"""

import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import Request
from fastapi.responses import JSONResponse

from src.common.errors import error_response

logger = logging.getLogger(__name__)


class SlidingWindowRateLimiter:
    """Thread-safe sliding window rate limiter."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        """Check if request for key is allowed and record hit."""
        now = datetime.now(UTC).timestamp()
        cutoff = now - self.window_seconds

        with self._lock:
            timestamps = self._hits[key]
            # Prune timestamps older than window
            valid_timestamps = [ts for ts in timestamps if ts > cutoff]

            if len(valid_timestamps) >= self.max_requests:
                self._hits[key] = valid_timestamps
                return False

            valid_timestamps.append(now)
            self._hits[key] = valid_timestamps
            return True

    def reset(self) -> None:
        """Clear all recorded hits (useful for testing)."""
        with self._lock:
            self._hits.clear()


def get_client_ip(request: Request, trusted_proxies: list[str] | set[str] | None = None) -> str:
    """Extract client IP safely from request.

    Only respects X-Forwarded-For if the direct socket peer is an explicitly trusted proxy.
    Otherwise, returns the direct socket host to prevent client-spoofed bypasses.
    """
    direct_ip = request.client.host if request.client and request.client.host else "127.0.0.1"
    if trusted_proxies and direct_ip in trusted_proxies:
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            # Client IP is the first entry in X-Forwarded-For list
            client_ip = xff.split(",")[0].strip()
            if client_ip:
                return client_ip
    return direct_ip


def create_rate_limit_checker(
    limiter: SlidingWindowRateLimiter,
    action_name: str,
    key_func: Callable[[Request], str] | None = None,
) -> Callable[[Request], JSONResponse | None]:
    """Helper to check rate limit and return 429 response if exceeded."""

    def check(request: Request) -> JSONResponse | None:
        key = key_func(request) if key_func else f"{get_client_ip(request)}:{action_name}"
        if not limiter.is_allowed(key):
            logger.warning("Rate limit exceeded for action '%s', key '%s'", action_name, key)
            return error_response(
                status_code=429,
                code="RATE_LIMITED",
                message=f"Too many requests for {action_name}. Please try again later.",
                request=request,
            )
        return None

    return check
